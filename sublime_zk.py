import sublime, sublime_plugin, os, re, subprocess, glob, datetime

# for highlighting
import threading

def timestamp():
    return '{:%Y%m%d%H%M}'.format(datetime.datetime.now())

def create_note(filn, title):
    with open(filn, mode='w', encoding='utf-8') as f:
        f.write(u'# {}\ntags = \n\n'.format(title))

def get_path_for(view):
    folder = None
    if view.window().project_file_name():
        folder = os.path.dirname(view.window().project_file_name())
    elif view.file_name():
        folder = os.path.dirname(view.file_name())
    elif view.window().folders():
        folder = os.path.abspath(view.window().folders()[0])
    return folder

class FollowWikiLinkCommand(sublime_plugin.TextCommand):
    """
    Command that opens the note corresponding to a link the cursor is placed in.
    """
    Link_Prefix = '['
    Link_Prefix_Len = len(Link_Prefix)
    Link_Postfix = ']'

    def select_link(self):
        region = self.view.sel()[0]

        cursor_pos = region.begin()
        line_region = self.view.line(cursor_pos)
        line_start = line_region.begin()

        linestart_till_cursor_str = self.view.substr(sublime.Region(line_start,
            cursor_pos))
        full_line = self.view.substr(line_region)

        # search backwards from the cursor until we find [[
        brackets_start = linestart_till_cursor_str.rfind(
            FollowWikiLinkCommand.Link_Prefix)

        # search backwards from the cursor until we find ]]
        # finding ]] would mean that we are outside of the link, behind the ]]
        brackets_end_in_the_way = linestart_till_cursor_str.rfind(
            FollowWikiLinkCommand.Link_Postfix)

        if brackets_end_in_the_way > brackets_start:
            # behind closing brackets, finding the link would be unexpected
            return

        if brackets_start >= 0:
            brackets_end = full_line[brackets_start:].find(
                FollowWikiLinkCommand.Link_Postfix)

            if brackets_end >= 0:
                link_region = sublime.Region(line_start + brackets_start +
                    FollowWikiLinkCommand.Link_Prefix_Len,
                    line_start + brackets_start + brackets_end)
                return link_region
        return

    def run(self, edit):
        folder = get_path_for(self.view)
        if not folder:
            return

        settings = sublime.load_settings('sublime_zk.sublime-settings')
        extension = settings.get('wiki_extension')
        id_in_title = settings.get('id_in_title')

        window = self.view.window()
        location = self.select_link()

        if location is None:
            # no link found, not between brackets
            return

        selected_text = self.view.substr(location)

        # search for file starting with text between the brackets (usually
        # the ID)
        the_file = os.path.join(folder, selected_text + '*')
        candidates = [f for f in glob.glob(the_file) if f.endswith(extension)]
        # print('Candidates: for glob {} : {}'.format(the_file, candidates))

        if len(candidates) > 0:
            the_file = candidates[0]
            new_view = window.open_file(the_file)
        else:
            # suppose you have entered "[[my new note]]", then we are going to
            # create "201710201631 my new note.md". We will also add a link
            # "[[201710201631]]" into the current document
            new_id = timestamp()
            the_file = new_id + ' ' + selected_text + extension
            the_file = os.path.join(folder, the_file)
            self.view.replace(edit, location, new_id)

            if id_in_title:
                selected_text = new_id + ' ' + selected_text

            create_note(the_file, selected_text)
            new_view = window.open_file(the_file)


class NewZettelCommand(sublime_plugin.WindowCommand):
    """
    Command that prompts for a note title and then creates a note with that
    title.
    """
    def run(self):
        self.window.show_input_panel('New Note:', '', self.on_done, None, None)

    def on_done(self, input_text):
        # sanity check: do we have a project
        if self.window.project_file_name():
            # yes we have a project!
            folder = os.path.dirname(self.window.project_file_name())
        # sanity check: do we have an open folder
        elif self.window.folders():
            # yes we have an open folder!
            folder = os.path.abspath(self.window.folders()[0])
        else:
            # no folder or project. try to create one
            self.window.run_command('save_project_as')
            # if after that we still have no project (user canceled save as)
            folder = os.path.dirname(self.window.project_file_name())
            if not folder:
                # I don't know how to save_as the file so there's nothing sane I
                # can do here. So: non-obtrusively warn the user that this failed
                self.window.status_message(
                'New note cannot be created without a project or an open folder!')
                return

        settings = sublime.load_settings('sublime_zk.sublime-settings')
        extension = settings.get('wiki_extension')
        id_in_title = settings.get('id_in_title')

        new_id = timestamp()
        the_file = os.path.join(folder,  new_id + ' ' + input_text + extension)

        if id_in_title:
            input_text = new_id + ' ' + input_text

        create_note(the_file, input_text)
        new_view = self.window.open_file(the_file)



class GetWikiLinkCommand(sublime_plugin.TextCommand):
    """
    Command that lets you choose one of all your notes and inserts a link to
    the chosen note.
    """
    def on_done(self, selection):
        if selection == -1:
            self.view.run_command(
                'insert_wiki_link', {'args': {'text': '[['}})
            return

        settings = sublime.load_settings('sublime_zk.sublime-settings')
        prefix = '[['
        postfix = ']]'
        if not settings.get('double_brackets', True):
            prefix = '['
            postfix = ']'

        # return only the id or whatever comes before the first blank
        link_txt = prefix + self.modified_files[selection].split(' ', 1)[0] \
                                                                       + postfix
        self.view.run_command(
            'insert_wiki_link', {'args': {'text': link_txt}})

    def run(self, edit):
        folder = get_path_for(self.view)
        if not folder:
            return

        settings = sublime.load_settings('sublime_zk.sublime-settings')
        extension = settings.get('wiki_extension')

        self.files = [f for f in os.listdir(folder) if f.endswith(extension)]
        self.modified_files = [f.replace(extension, '') for f in self.files]
        self.view.window().show_quick_panel(self.modified_files, self.on_done)



class InsertWikiLinkCommand(sublime_plugin.TextCommand):
    """
    Command that just inserts text, usually a link to a note.
    """
    def run(self, edit, args):
        self.view.insert(edit, self.view.sel()[0].begin(), args['text'])



class NoteLinkHighlighter(sublime_plugin.EventListener):
    """
    Receives all updates to all views.
    * Highlights [[201710310102]] style links.
    * Enables word completion (ctrl + space) to insert links to notes
    """
    DEFAULT_MAX_LINKS = 1000

    note_links_for_view = {}
    scopes_for_view = {}
    ignored_views = []
    highlight_semaphore = threading.Semaphore()

    def on_query_completions(self, view, prefix, locations):
        """
        Generate auto-completion entries for markdown files, based on
        """
        point = locations[0]
        if view.match_selector(point, 'text.html.markdown') == 0:
            return

        folder = get_path_for(view)
        if not folder:
            return []

        # we have a path and are in markdown!
        settings = sublime.load_settings('sublime_zk.sublime-settings')
        prefix = '[['
        postfix = ']]'
        if not settings.get('double_brackets', True):
            prefix = '['
            postfix = ']'

        extension = settings.get('wiki_extension')
        completions = []
        ids_and_names = [f.split(' ', 1) for f in os.listdir(folder)
                                            if f.endswith(extension)]
        for noteid, notename in ids_and_names:
            completions.append([noteid + ' ' + notename,
                prefix + noteid + postfix])
        return (completions, sublime.INHIBIT_WORD_COMPLETIONS)

    def on_activated(self, view):
        self.update_note_link_highlights(view)

    # Async listeners for ST3
    def on_load_async(self, view):
        self.update_note_link_highlights_async(view)

    def on_modified_async(self, view):
        self.update_note_link_highlights_async(view)

    def on_close(self, view):
        for map in [self.note_links_for_view, self.scopes_for_view,
                    self.ignored_views]:
            if view.id() in map:
                del map[view.id()]

    """The entry point. Find all LINKs in view, store and highlight them"""
    def update_note_link_highlights(self, view):
        settings = sublime.load_settings('sublime_zk.sublime-settings')
        should_highlight = settings.get('highlight_note_links')

        max_note_link_limit = NoteLinkHighlighter.DEFAULT_MAX_LINKS
        if view.id() in NoteLinkHighlighter.ignored_views:
            return

        note_links = view.find_by_selector('markup.zettel')
        # update the regions to ignore the brackets
        note_links = [sublime.Region(n.a, n.b) for n in note_links]

        # Avoid slowdowns for views with too many links
        n_links = len(note_links)
        if n_links > max_note_link_limit:
            print('NoteLinkHighlighter: ignoring view with %d links' % n_links)
            NoteLinkHighlighter.ignored_views.append(view.id())
            return

        NoteLinkHighlighter.note_links_for_view[view.id()] = note_links

        if (should_highlight):
            self.highlight_note_links(view, note_links)

    def update_note_link_highlights_async(self, view):
        NoteLinkHighlighter.highlight_semaphore.acquire()
        try:
            self.update_note_link_highlights(view)
        finally:
            NoteLinkHighlighter.highlight_semaphore.release()

    """
    Creates a set of regions from the intersection of note_links and scopes,
    underlines all of them.
    """
    def highlight_note_links(self, view, note_links):
        settings = sublime.load_settings('sublime_zk.sublime-settings')
        show_bookmarks = settings.get('show_bookmarks_in_gutter')

        # We need separate regions for each lexical scope for ST to use a
        # proper color for the underline
        scope_map = {}
        for note_link in note_links:
            scope_name = view.scope_name(note_link.a)
            scope_map.setdefault(scope_name, []).append(note_link)

        for scope_name in scope_map:
            self.underline_regions(view, scope_name, scope_map[scope_name],
                show_bookmarks)

        self.update_view_scopes(view, scope_map.keys())

    """Apply underlining to provided regions."""
    def underline_regions(self, view, scope_name, regions, show_bookmarks):
        if show_bookmarks:
            symbol = 'bookmark'
        else:
            symbol = ''

        view.add_regions(
            u'clickable-note_links ' + scope_name,
            regions,
            # the scope name for nice links different from external links
            "markup.zettel.link",
            symbol,
            flags=sublime.DRAW_NO_FILL |
                  sublime.DRAW_NO_OUTLINE | sublime.DRAW_SOLID_UNDERLINE)

    """
    Store new set of underlined scopes for view.
    Erase underlining from scopes that were once used but are not anymore.
    """
    def update_view_scopes(self, view, new_scopes):
        old_scopes = NoteLinkHighlighter.scopes_for_view.get(view.id(), None)
        if old_scopes:
            unused_scopes = set(old_scopes) - set(new_scopes)
            for unused_scope_name in unused_scopes:
                view.erase_regions(u'clickable-note_links ' + unused_scope_name)
        NoteLinkHighlighter.scopes_for_view[view.id()] = new_scopes
