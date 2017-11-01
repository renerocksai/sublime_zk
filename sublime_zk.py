import sublime, sublime_plugin, os, re, subprocess, glob, datetime

# for highlighting
import threading

def timestamp():
    return '{:%Y%m%d%H%M}'.format(datetime.datetime.now())

def create_note(filn, title):
    with open(filn, mode='w', encoding='utf-8') as f:
        f.write(u'# {}\ntags = \n\n'.format(title))


class FollowWikiLinkCommand(sublime_plugin.TextCommand):
    def select_link(self):
        region = self.view.sel()[0]

        cursor_pos = region.begin()
        line_region = self.view.line(cursor_pos)
        line_start = line_region.begin()

        linestart_till_cursor_str = self.view.substr(sublime.Region(line_start, cursor_pos))
        full_line = self.view.substr(line_region)

        # search backwards from the cursor until we find [[hello world]]
        brackets_start = linestart_till_cursor_str.rfind('[[')
        brackets_end_in_the_way = linestart_till_cursor_str.rfind(']]')
        if brackets_end_in_the_way > brackets_start:
            # we're behind closing brackets, finding the link would be unexpected
            return
        if brackets_start >= 0:
            brackets_end = full_line[brackets_start:].find(']]')
            if brackets_end >= 0:
                link_region = sublime.Region(line_start + brackets_start+2, line_start + brackets_start + brackets_end)
                return link_region
        return


    def run(self, edit):
        settings = sublime.load_settings('sublime_zk.sublime-settings')
        directory = os.path.dirname(self.view.window().project_file_name())
        extension = settings.get('wiki_extension')
        id_in_title = settings.get('id_in_title', "false").lower() != "false"

        window = self.view.window()

        location = self.select_link()
        
        if location is None:
            # no link found, not between brackets   
            return

        selected_text = self.view.substr(location)

        # search for file starting with text between the brackets (usually the ID)
        the_file = os.path.join(directory, selected_text + '*') 
        candidates = [f for f in glob.glob(the_file) if f.endswith(extension)]
        # print('Candidates: for glob {} : {}'.format(the_file, candidates))
        if len(candidates) > 0:
            the_file = candidates[0]
            #open the existing note.
            new_view = window.open_file(the_file)
        else:
            # suppose you have entered "[[my new note]]", then we are going to create
            # "201710201631 my new note.md". we will also add a link "[[201710201631]] into the current document"

            new_id = timestamp()
            the_file = os.path.join(directory, new_id + ' ' + selected_text + extension)
            self.view.replace(edit, location, new_id)
            if id_in_title:
                selected_text = new_id + ' ' + selected_text
            create_note(the_file, selected_text)
            new_view = window.open_file(the_file)


class NewZettelCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.window.show_input_panel('New Note:', '', self.on_done, None, None)

    def on_done(self, input_text):
        settings = sublime.load_settings('sublime_zk.sublime-settings')
        directory = os.path.dirname(self.window.project_file_name())
        extension = settings.get('wiki_extension')
        id_in_title = settings.get('id_in_title', "false").lower() != "false"

        new_id = timestamp()
        the_file = os.path.join(directory,  new_id + ' ' + input_text + extension)

        if id_in_title:
            input_text = new_id + ' ' + input_text
        create_note(the_file, input_text)
        new_view = self.window.open_file(the_file)

class GetWikiLinkCommand(sublime_plugin.TextCommand):
    def on_done(self, selection):
        if selection == -1:
            self.view.run_command(
                "insert_wiki_link", {"args":
                {'text': '[['}})
            return
        self.view.run_command(
            "insert_wiki_link", {"args":
            # return only the id or whatever comes before the first blank
            {'text': '[['+self.modified_files[selection].split(' ', 1)[0]+']]'}})

    def run(self, edit):
        settings = sublime.load_settings('sublime_zk.sublime-settings')
        directory = os.path.dirname(self.view.window().project_file_name())
        extension = settings.get('wiki_extension')

        self.outputText = '[['
        self.files = [f for f in os.listdir(directory) if f.endswith(extension)]
        self.modified_files = [item.replace(extension,"") for item in self.files]
        self.view.window().show_quick_panel(self.modified_files, self.on_done)



class InsertWikiLinkCommand(sublime_plugin.TextCommand):
    def run(self, edit, args):
        self.view.insert(edit, self.view.sel()[0].begin(), args['text'])









class NoteLinkHighlighter(sublime_plugin.EventListener):
    LINK_REGEX = r"(\[\[)[0-9]{12}(\]\])"
    DEFAULT_MAX_LINKS = 1000

    note_links_for_view = {}
    scopes_for_view = {}
    ignored_views = []
    highlight_semaphore = threading.Semaphore()

    def on_activated(self, view):
        self.update_note_link_highlights(view)

    # Async listeners for ST3
    def on_load_async(self, view):
        self.update_note_link_highlights_async(view)

    def on_modified_async(self, view):
        self.update_note_link_highlights_async(view)

    def on_close(self, view):
        for map in [self.note_links_for_view, self.scopes_for_view, self.ignored_views]:
            if view.id() in map:
                del map[view.id()]

    """The logic entry point. Find all LINKs in view, store and highlight them"""
    def update_note_link_highlights(self, view):
        settings = sublime.load_settings('sublime_zk.sublime-settings')
        should_highlight_note_links = settings.get('highlight_note_links', "True")
        should_highlight_note_links = should_highlight_note_links.lower() != "false"

        max_note_link_limit = NoteLinkHighlighter.DEFAULT_MAX_LINKS
        if view.id() in NoteLinkHighlighter.ignored_views:
            return

        note_links = view.find_all(NoteLinkHighlighter.LINK_REGEX)

        # update the regions to ignore the brackets
        note_links = [sublime.Region(n.a+2, n.b-2) for n in note_links]

        # Avoid slowdowns for views with too many LINKs
        if len(note_links) > max_note_link_limit:
            print("NoteLinkHighlighter: ignoring view with %u links" % len(note_links))
            NoteLinkHighlighter.ignored_views.append(view.id())
            return

        NoteLinkHighlighter.note_links_for_view[view.id()] = note_links

        if (should_highlight_note_links):
            self.highlight_note_links(view, note_links)

    def update_note_link_highlights_async(self, view):
        NoteLinkHighlighter.highlight_semaphore.acquire()
        try:
            self.update_note_link_highlights(view)
        finally:
            NoteLinkHighlighter.highlight_semaphore.release()

    """Creates a set of regions from the intersection of note_links and scopes,
    underlines all of them."""
    def highlight_note_links(self, view, note_links):
        settings = sublime.load_settings('sublime_zk.sublime-settings')
        show_bookmarks_in_gutter = settings.get('show_bookmarks_in_gutter', "True")
        show_bookmarks_in_gutter = show_bookmarks_in_gutter.lower() != "false"

        # We need separate regions for each lexical scope for ST to use a proper color for the underline
        scope_map = {}
        for note_link in note_links:
            scope_name = view.scope_name(note_link.a)
            scope_map.setdefault(scope_name, []).append(note_link)

        for scope_name in scope_map:
            self.underline_regions(view, scope_name, scope_map[scope_name], show_bookmarks_in_gutter)

        self.update_view_scopes(view, scope_map.keys())

    """Apply underlining to provided regions."""
    def underline_regions(self, view, scope_name, regions, show_bookmarks=True):
        if show_bookmarks:
            symbol = 'bookmark'
        else:
            symbol = ''

        view.add_regions(
            u'clickable-note_links ' + scope_name,
            regions,
            #scope_name + 
            "markup.bold",
            symbol,
            flags=sublime.DRAW_NO_FILL|sublime.DRAW_NO_OUTLINE|sublime.DRAW_SOLID_UNDERLINE)

    """Store new set of underlined scopes for view. Erase underlining from
    scopes that were used but are not anymore."""
    def update_view_scopes(self, view, new_scopes):
        old_scopes = NoteLinkHighlighter.scopes_for_view.get(view.id(), None)
        if old_scopes:
            unused_scopes = set(old_scopes) - set(new_scopes)
            for unused_scope_name in unused_scopes:
                view.erase_regions(u'clickable-note_links ' + unused_scope_name)

        NoteLinkHighlighter.scopes_for_view[view.id()] = new_scopes