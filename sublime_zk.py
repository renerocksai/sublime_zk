import sublime, sublime_plugin, os, re, subprocess, glob, datetime


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
            create_note(the_file, selected_text)
            new_view = window.open_file(the_file)


class NewZettelCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.window.show_input_panel('New Note:', '', self.on_done, None, None)

    def on_done(self, input_text):
        settings = sublime.load_settings('sublime_zk.sublime-settings')
        directory = os.path.dirname(self.window.project_file_name())
        extension = settings.get('wiki_extension')

        the_file = os.path.join(directory, timestamp() + ' ' + input_text + extension)
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

