"""
                  ___.   .__  .__                            __
        ________ _\_ |__ |  | |__| _____   ____      _______|  | __
       /  ___/  |  \ __ \|  | |  |/     \_/ __ \     \___   /  |/ /
       \___ \|  |  / \_\ \  |_|  |  Y Y  \  ___/      /    /|    <
      /____  >____/|___  /____/__|__|_|  /\___  >____/_____ \__|_ \
           \/          \/              \/     \/_____/     \/    \/
"""
import sublime, sublime_plugin, os, re, subprocess, glob, datetime
import threading


class ZkConstants:
    """
    Some constants used over and over
    """
    Link_Prefix = '['
    Link_Prefix_Len = len(Link_Prefix)
    Link_Postfix = ']'
    Tag_Stops = '.,\/!$%\^&\*;:\{\}[]\'"=`~()<>\\'

    Link_Matcher = re.compile('(\[+|§)([0-9]{12})(\]+|.)')
    # Above RE doesn't really care about closing ] andymore
    # This works in our favour so we support [[201711122259 This is a note]]
    # when expanding overview notes


class ExternalSearch:
    """
    Static class to group all external search related functions.
    """
    SEARCH_COMMAND = 'ag'
    RE_TAGS = r"(?<=\s|^)(?<!`)(#+[^#\s.,\/!$%\^&\*;:{}\[\]'\"=`~()<>”\\]+)"
    EXTERNALIZE = '.search_results.md'   # '' to skip

    @staticmethod
    def search_all_tags(folder, extension):
        """
        Create a list of all #tags of all notes in folder.
        """
        output = ExternalSearch.search_in(folder, ExternalSearch.RE_TAGS,
            extension, tags=True)
        tags = set()
        for line in output.split('\n'):
            if line:
                tags.add(line)
        if ExternalSearch.EXTERNALIZE:
            with open(ExternalSearch.external_file(folder), mode='w',
                    encoding='utf-8') as f:
                for tag in sorted(tags):
                    f.write(u' {}\n'.format(tag))
        return list(tags)

    @staticmethod
    def search_tagged_notes(folder, extension, tag):
        """
        Return a list of note files containing #tag.
        """
        output = ExternalSearch.search_in(folder, tag, extension)
        prefix = 'Notes tagged with {}:'.format(tag)
        ExternalSearch.externalize_note_links(output, folder, prefix)
        return output.split('\n')

    @staticmethod
    def search_friend_notes(folder, extension, note_id):
        """
        Return a list of notes referencing note_id.
        """
        regexp = '(\[' + note_id + '\])|(§' + note_id + ')'
        output = ExternalSearch.search_in(folder, regexp, extension)
        settings = sublime.load_settings('sublime_zk.sublime-settings')
        link_prefix = '[['
        link_postfix = ']]'
        if not settings.get('double_brackets', True):
            link_prefix = '['
            link_postfix = ']'
        prefix = 'Notes referencing {}{}{}:'.format(link_prefix, note_id,
            link_postfix)
        ExternalSearch.externalize_note_links(output, folder, prefix)
        return output.split('\n')

    @staticmethod
    def search_in(folder, regexp, extension, tags=False):
        """
        Perform an external search for regexp in folder.

        tags == True : only matching words are returned.
        tags == False: only names of files with matches are returned.
        """
        args = [ExternalSearch.SEARCH_COMMAND, '--nocolor']
        if tags:
            args.extend(['--nofilename', '--nonumbers', '-o'])
        else:
            args.extend(['-l'])
        # args.extend(['--silent', '--' + extension[1:], regexp,
        args.extend(['--silent', '--markdown', regexp,
            folder])
        return ExternalSearch.run(args, folder)

    @staticmethod
    def run(args, folder):
        """
        Execute ag to run a search, handle errors & timeouts.
        Return output of stdout as string.
        """
        output = b''
        try:
            output = subprocess.check_output(args, shell=False, timeout=10000)
        except subprocess.CalledProcessError as e:
            print('sublime_zk: search unsuccessful:')
            print(e.returncode)
            print(e.cmd)
            for line in e.output.decode('utf-8').split('\n'):
                print('    ', line)
        except subprocess.TimeoutExpired:
            print('sublime_zk: search timed out:', ' '.join(args))
        return output.decode('utf-8')

    @staticmethod
    def externalize_note_links(ag_out, folder, prefix=None):
        """
        If enabled, write ag file name output into external search results file
        in `[[note_id]] note title` style.
        """
        if ExternalSearch.EXTERNALIZE:
            settings = sublime.load_settings('sublime_zk.sublime-settings')
            extension = settings.get('wiki_extension')
            link_prefix = '[['
            link_postfix = ']]'
            if not settings.get('double_brackets', True):
                link_prefix = '['
                link_postfix = ']'

            with open(ExternalSearch.external_file(folder),
                mode='w', encoding='utf-8') as f:
                if prefix:
                    f.write(u'{}\n\n'.format(prefix))
                for line in ag_out.split('\n'):
                    if not line.strip():
                        continue
                    if line.endswith(extension):
                        line = line.replace(extension, '')
                    note_id, title = line.split(' ', 1)
                    note_id = os.path.basename(note_id)

                    f.write(u'{}{}{} {}\n'.format(link_prefix, note_id,
                        link_postfix, title))

    @staticmethod
    def external_file(folder):
        """ Return the name of the external search results file. """
        return os.path.join(folder, ExternalSearch.EXTERNALIZE)


# global magic
F_EXT_SEARCH = os.system('{} --help'.format(ExternalSearch.SEARCH_COMMAND)) == 0
if F_EXT_SEARCH:
    print('Sublime_ZK: Using ag!')
else:
    settings = sublime.load_settings('sublime_zk.sublime-settings')
    ag = settings.get('path_to_ag', '/usr/local/bin/ag')
    if ag:
        if os.system(ag + ' --help') == 0:
            ExternalSearch.SEARCH_COMMAND = ag
            F_EXT_SEARCH = True
            print('Sublime_ZK: Using ', ag)
        else:
            print('Sublime_ZK: Not using ag!')
    else:
        print('Sublime_ZK: Not using ag!')

def timestamp():
    return '{:%Y%m%d%H%M}'.format(datetime.datetime.now())

def create_note(filn, title):
    with open(filn, mode='w', encoding='utf-8') as f:
        f.write(u'# {}\ntags = \n\n'.format(title))

def get_path_for(view):
    """
    Try to find out and return the note archive path of the given view.
    """
    folder = None
    if view.window().project_file_name():
        folder = os.path.dirname(view.window().project_file_name())
    elif view.file_name():
        folder = os.path.dirname(view.file_name())
    elif view.window().folders():
        folder = os.path.abspath(view.window().folders()[0])
    return folder

def note_file_by_id(note_id, folder, extension):
    """
    Find the file for note_id.
    """
    the_file = os.path.join(folder, note_id + '*')
    candidates = [f for f in glob.glob(the_file) if f.endswith(extension)]
    if len(candidates) > 0:
        return candidates[0]

def extract_tags(file):
    """
    Extract #tags from file.
    Returns all words starting with `#`.
    """
    tags = set()
    with open(file, mode='r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            for word in line.split():
                if word.startswith('#') and not word.endswith('#'):
                    tags.add(word)
    return tags

def get_all_notes_for(folder, extension):
    """
    Return all files with extension in folder.
    """
    return [os.path.join(folder, f) for f in os.listdir(folder)
                                                    if f.endswith(extension)]

def find_all_tags_in(folder, extension):
    """
    Return a list of all #tags from all notes in folder using external search
    if possible.
    """
    global F_EXT_SEARCH
    if F_EXT_SEARCH:
        return ExternalSearch.search_all_tags(folder, extension)
    tags = set()
    for file in get_all_notes_for(folder, extension):
        tags |= extract_tags(file)
    return list(tags)


def tag_at(text, pos=None):
    """
    Search for a ####tag inside of text.
    If pos is given, searches for the tag at pos
    """
    if pos is None:
        search_text = text
    else:
        search_text = text[:pos + 1]
    # find first `#`
    inner = search_text.rfind('#')
    if inner >=0:
        # find next consecutive `#`
        for c in reversed(search_text[:inner]):
            if c != '#':
                break
            inner -=1
        # search end of tag
        end = inner
        for c in text[inner:]:
            if c.isspace() or c in ZkConstants.Tag_Stops:
                break
            end += 1
        tag = text[inner:end]

        # test if it's just a `# heading` (resulting in `#`) or a real tag
        if tag.replace('#', ''):
            return text[inner:end], (inner, end)
    return '', (None, None)


def select_link_in(view):
    """
    Used by different commands to select the link under the cursor, if
    any.
    Return the
    """
    region = view.sel()[0]

    cursor_pos = region.begin()
    line_region = view.line(cursor_pos)
    line_start = line_region.begin()

    linestart_till_cursor_str = view.substr(sublime.Region(line_start,
        cursor_pos))
    full_line = view.substr(line_region)

    # hack for § links
    p_symbol_pos = linestart_till_cursor_str.rfind('§')
    if p_symbol_pos >= 0:
        p_link_start = line_start + p_symbol_pos + 1
        p_link_end = p_link_start + 12
        # check if it's a numeric link
        if re.match('§[0-9]{12}', full_line[p_symbol_pos:]):
            return linestart_till_cursor_str, sublime.Region(p_link_start,
                p_link_end)

    # search backwards from the cursor until we find [[
    brackets_start = linestart_till_cursor_str.rfind(ZkConstants.Link_Prefix)

    # search backwards from the cursor until we find ]]
    # finding ]] would mean that we are outside of the link, behind the ]]
    brackets_end_in_the_way = linestart_till_cursor_str.rfind(
                                                    ZkConstants.Link_Postfix)

    if brackets_end_in_the_way > brackets_start:
        # behind closing brackets, finding the link would be unexpected
        return linestart_till_cursor_str, None

    if brackets_start >= 0:
        brackets_end = full_line[brackets_start:].find(ZkConstants.Link_Postfix)

        if brackets_end >= 0:
            link_region = sublime.Region(line_start + brackets_start +
                ZkConstants.Link_Prefix_Len,
                line_start + brackets_start + brackets_end)
            return  linestart_till_cursor_str, link_region
    return linestart_till_cursor_str, None


class TextProduction:
    """
    Static class grouping functions for text production from overview notes.
    """
    @staticmethod
    def read_full_note(note_id, folder, extension):
        """
        Return contents of note with ID note_id.
        """
        note_file = note_file_by_id(note_id, folder, extension)
        if not note_file:
            return None, None
        with open(note_file, mode='r', encoding='utf-8') as f:
            return note_file, f.read()

    @staticmethod
    def expand_links(text, folder, extension, replace_lines=False):
        """
        Expand all note-links in text, replacing their lines by note contents.
        """
        result_lines = []
        for line in text.split('\n'):
            link_results = ZkConstants.Link_Matcher.findall(line)
            if link_results:
                if not replace_lines:
                    result_lines.append(line)
                for pre, note_id, post in link_results:
                    note_file, content = TextProduction.read_full_note(note_id,
                        folder, extension)
                    if not content:
                        header = '\n<!-- Note not found: ' + note_id + ' -->'
                    else:
                        filename = os.path.basename(note_file).replace(
                            extension, '')
                        filename = filename.split(' ', 1)[1]
                        header = pre + note_id + post + ' ' + filename
                        header = '\n<!-- !    ' + header + '    -->'
                        footer = '<!-- (End of note ' + note_id + ') -->'
                        result_lines.append(header)
                        result_lines.extend(content.split('\n'))
                    result_lines.append(footer)
            else:
                result_lines.append(line)
        return '\n'.join(result_lines)

    def refresh_result(text, folder, extension):
        """
        Refresh the result of expand_links with current contents of referenced
        notes.
        """
        result_lines = []
        state = 'default'
        note_id = pre = post = None

        for line in text.split('\n'):
            if state == 'skip_lines':
                if not line.startswith('<!-- (End of note'):
                    continue
                # insert note
                note_file, content = TextProduction.read_full_note(note_id,
                    folder, extension)
                if not content:
                    header = '\n<!-- Note not found: ' + note_id + ' -->'
                else:
                    filename = os.path.basename(note_file).replace(
                        extension, '')
                    filename = filename.split(' ', 1)[1]
                    header = pre + note_id + post + ' ' + filename
                    header = '<!-- !    ' + header + '    -->'
                    footer = '<!-- (End of note ' + note_id + ') -->'
                    result_lines.append(header)
                    result_lines.extend(content.split('\n'))
                result_lines.append(footer)
                state = 'default'
                continue

            if line.startswith('<!-- !'):
                # get note id
                note_links = ZkConstants.Link_Matcher.findall(line)
                if note_links:
                    pre, note_id, post = note_links[0]
                    state = 'skip_lines'
            else:
                result_lines.append(line)
        return '\n'.join(result_lines)


class ExpandOverviewNoteCommand(sublime_plugin.TextCommand):
    """
    Command for expanding overview notes.
    """
    def run(self, edit):
        folder = get_path_for(self.view)
        if not folder:
            return

        settings = sublime.load_settings('sublime_zk.sublime-settings')
        extension = settings.get('wiki_extension')

        complete_text = self.view.substr(sublime.Region(0, self.view.size()))
        result_text = TextProduction.expand_links(complete_text, folder,
            extension, replace_lines=True)
        new_view = self.view.window().new_file()

        # don't: this causes auto-indent:
        # new_view.run_command("insert", {"characters": result_text})
        new_view.insert(edit, 0, result_text)   # no auto-indent
        # set syntax late, seems to speed insertion up
        new_view.set_syntax_file(
            'Packages/sublime_zk/sublime_zk.sublime-syntax')


class RefreshExpandedNoteCommand(sublime_plugin.TextCommand):
    """
    Command for refreshing expanded overview notes.
    """
    def run(self, edit):
        folder = get_path_for(self.view)
        if not folder:
            return

        settings = sublime.load_settings('sublime_zk.sublime-settings')
        extension = settings.get('wiki_extension')
        complete_region = sublime.Region(0, self.view.size())
        complete_text = self.view.substr(complete_region)
        result_text = TextProduction.refresh_result(complete_text, folder,
            extension)
        self.view.replace(edit, complete_region, result_text)


class FollowWikiLinkCommand(sublime_plugin.TextCommand):
    """
    Command that opens the note corresponding to a link the cursor is placed in
    or searches for the tag under the cursor.
    """
    def on_done(self, selection):
        """
        Called when the link was a tag, a tag picker overlay was displayed, and
        a tag was selected by the user.
        """
        if selection == -1:
            return
        the_file = os.path.join(self.folder, self.tagged_note_files[selection])
        new_view = self.view.window().open_file(the_file)

    def select_link(self):
        """
        Select a note-link under the cursor.
        If it's a tag, follow it by searching for tagged notes.
        Search:
        * via find-in-files if ag is not found
        * results in external search results file if enabled
        * else present overlay to pick a note
        """
        global F_EXT_SEARCH
        linestart_till_cursor_str, link_region = select_link_in(self.view)
        if link_region:
            return link_region

        # test if we are supposed to follow a tag
        if '#' in linestart_till_cursor_str:
            view = self.view
            cursor_pos = view.sel()[0].begin()
            line_region = view.line(cursor_pos)
            line_start = line_region.begin()
            full_line = view.substr(line_region)
            cursor_pos_in_line = cursor_pos - line_start
            tag, (begin, end) = tag_at(full_line, cursor_pos_in_line)
            if not tag:
                return

            settings = sublime.load_settings('sublime_zk.sublime-settings')

            if F_EXT_SEARCH:
                extension = settings.get('wiki_extension')
                folder = get_path_for(self.view)
                if not folder:
                    return
                self.folder = folder
                self.tagged_note_files = ExternalSearch.search_tagged_notes(
                    folder, extension, tag)
                if ExternalSearch.EXTERNALIZE:
                    self.view.window().open_file(ExternalSearch.external_file(
                        folder))
                else:
                    self.tagged_note_files = [os.path.basename(f) for f in
                        self.tagged_note_files]
                    self.view.window().show_quick_panel(self.tagged_note_files,
                        self.on_done)
            else:
                new_tab = settings.get('show_search_results_in_new_tab')

                # hack for the find in files panel: select tag in view, copy it
                selection = self.view.sel()
                selection.clear()
                line_region = sublime.Region(line_start +begin, line_start +end)
                selection.add()
                self.view.window().run_command("copy")
                self.view.window().run_command("show_panel",
                    {"panel": "find_in_files",
                    "where": get_path_for(self.view),
                    "use_buffer": new_tab,})
                # now paste the tag --> it will land in the "find" field
                self.view.window().run_command("paste")
        return

    def run(self, edit, event=None):
        """
        Follow a note-link by:
        * opening its corresponding note [[note_id]]
        * creating a new note with [[title of new note]]
        * searching for #tagged notes
        depending on what's under the cursor
        """
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
        the_file = note_file_by_id(selected_text, folder, extension)

        if the_file:
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

    def want_event(self):
        # unused
        return True


class ShowReferencingNotesCommand(sublime_plugin.TextCommand):
    """
    Command searching for notes referencing the note id under the cursor.
    * if ag is not installed, opens a find-in-files for link under cursor.
    * if ag is installed, it will show results:
      * in an overlay if external search results are disabled
      * else in the external search file
    """
    def on_done(self, selection):
        """
        Called when a note was selected from the overlay:
        Open the selected note, if any.
        """
        if selection == -1:
            return
        the_file = os.path.join(self.folder, self.friend_note_files[selection])
        new_view = self.view.window().open_file(the_file)

    def run(self, edit):
        """
        Try to select note link if present. Search for notes as described above.
        """
        global F_EXT_SEARCH
        linestart_till_cursor_str, link_region = select_link_in(self.view)
        if not link_region:
            return

        settings = sublime.load_settings('sublime_zk.sublime-settings')
        if F_EXT_SEARCH:
            extension = settings.get('wiki_extension')
            folder = get_path_for(self.view)
            if not folder:
                return
            self.folder = folder
            note_id = self.view.substr(link_region)
            self.friend_note_files = ExternalSearch.search_friend_notes(
                folder, extension, note_id)
            self.friend_note_files = [os.path.basename(f) for f in
                self.friend_note_files]
            if ExternalSearch.EXTERNALIZE:
                self.view.window().open_file(ExternalSearch.external_file(
                    folder))
            else:
                self.view.window().show_quick_panel(self.friend_note_files,
                    self.on_done)
        else:
            new_tab = settings.get('show_search_results_in_new_tab')

            # hack for the find in files panel: select tag in view, copy it
            selection = self.view.sel()
            selection.clear()
            selection.add(link_region)
            self.view.window().run_command("copy")
            self.view.window().run_command("show_panel",
                {"panel": "find_in_files",
                "where": get_path_for(self.view),
                "use_buffer": new_tab,})
            # now paste the note-id --> it will land in the "find" field
            self.view.window().run_command("paste")
        return


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


class ZkTagSelectorCommand(sublime_plugin.TextCommand):
    """
    Command that lets you choose one of all your tags and inserts it.
    """
    def on_done(self, selection):
        if selection == -1:
            self.view.run_command(
                'insert_wiki_link', {'args': {'text': '#'}})   # can be re-used
            return

        # return only the id or whatever comes before the first blank
        tag_txt = self.tags[selection]
        self.view.run_command(
            'insert_wiki_link', {'args': {'text': tag_txt}})  # re-use of cmd

    def run(self, edit):
        folder = get_path_for(self.view)
        if not folder:
            return
        settings = sublime.load_settings('sublime_zk.sublime-settings')
        extension = settings.get('wiki_extension')
        self.tags = find_all_tags_in(folder, extension)
        self.view.window().show_quick_panel(self.tags, self.on_done)


class ZkShowAllTagsCommand(sublime_plugin.WindowCommand):
    """
    Command that creates a new view containing a sorted list of all tags
    in all notes
    """
    def run(self):
        # sanity check: do we have a project
        if self.window.project_file_name():
            # yes we have a project!
            folder = os.path.dirname(self.window.project_file_name())
        # sanity check: do we have an open folder
        elif self.window.folders():
            # yes we have an open folder!
            folder = os.path.abspath(self.window.folders()[0])
        else:
            # don't know where to grep
            return
        settings = sublime.load_settings('sublime_zk.sublime-settings')
        extension = settings.get('wiki_extension')
        new_pane = settings.get('show_all_tags_in_new_pane')

        tags = find_all_tags_in(folder, extension)
        if ExternalSearch.EXTERNALIZE:
            self.window.open_file(ExternalSearch.external_file(folder))
        else:
            tags.sort()
            if new_pane:
                self.window.run_command('set_layout', {
                    'cols': [0.0, 0.5, 1.0],
                    'rows': [0.0, 1.0],
                    'cells': [[0, 0, 1, 1], [1, 0, 2, 1]]
                })
                # goto right-hand pane
                self.window.focus_group(1)
            tagview = self.window.new_file()
            tagview.set_name('Tags')
            tagview.set_scratch(True)
            tagview.run_command("insert",{"characters": ' ' + '\n'.join(tags)})
            tagview.set_syntax_file(
                'Packages/sublime_zk/sublime_zk.sublime-syntax')
            # return back to note
            self.window.focus_group(0)


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
                                            if f.endswith(extension)
                                            and ' ' in f]
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

    def update_note_link_highlights(self, view):
        """
        The entry point. Find all LINKs in view, store and highlight them
        """
        settings = sublime.load_settings('sublime_zk.sublime-settings')
        should_highlight = settings.get('highlight_note_links')

        max_note_link_limit = NoteLinkHighlighter.DEFAULT_MAX_LINKS
        if view.id() in NoteLinkHighlighter.ignored_views:
            return

        note_links = view.find_by_selector('markup.zettel.link')
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

    def highlight_note_links(self, view, note_links):
        """
        Creates a set of regions from the intersection of note_links and scopes,
        underlines all of them.
        """
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

    def underline_regions(self, view, scope_name, regions, show_bookmarks):
        """
        Apply underlining to provided regions.
        """
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

    def update_view_scopes(self, view, new_scopes):
        """
        Store new set of underlined scopes for view.
        Erase underlining from scopes that were once used but are not anymore.
        """
        old_scopes = NoteLinkHighlighter.scopes_for_view.get(view.id(), None)
        if old_scopes:
            unused_scopes = set(old_scopes) - set(new_scopes)
            for unused_scope_name in unused_scopes:
                view.erase_regions(u'clickable-note_links ' + unused_scope_name)
        NoteLinkHighlighter.scopes_for_view[view.id()] = new_scopes
