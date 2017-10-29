import sublime, sublime_plugin, os, re, subprocess, glob, datetime



def timestamp():
    return '{:%Y%m%d%H%M}'.format(datetime.datetime.now())


class FollowWikiLinkCommand(sublime_plugin.TextCommand):

    def run(self, edit):
        settings = sublime.load_settings('MyWiki.sublime-settings')
        directory = settings.get('wiki_directory')
        directory = os.path.expanduser(directory)
        extension = settings.get('wiki_extension')
        window = self.view.window()

        oldLocation = self.view.sel()[0]
        self.view.run_command("bracketeer_select")
        location = self.view.sel()[0]
        selected_text = self.view.substr(location)
        self.view.sel().clear()
        self.view.sel().add(oldLocation)

        # search for file starting with text between the brackets (usually the ID)
        the_file = directory+selected_text + '*'    
        candidates = glob.glob(the_file)

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
            # open(the_file, "a")   # un-comment if you want to create an empty file
            new_view = window.open_file(the_file)


class NewZettelCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.window.show_input_panel('Neuer Zettel', '', self.on_done, None, None)

    def on_done(self, input_text):
        settings = sublime.load_settings('MyWiki.sublime-settings')
        directory = settings.get('wiki_directory')
        directory = os.path.expanduser(directory)
        extension = settings.get('wiki_extension')

        the_file = os.path.join(directory, timestamp() + ' ' + input_text + extension)
        #open(the_file, "a")
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
        settings = sublime.load_settings('MyWiki.sublime-settings')
        directory = settings.get('wiki_directory')
        directory = os.path.expanduser(directory)
        extension = settings.get('wiki_extension')

        self.outputText = '[['
        self.files = os.listdir(directory)
        self.modified_files = [item.replace(extension,"") for item in self.files]
        self.view.window().show_quick_panel(self.modified_files, self.on_done)



class InsertWikiLinkCommand(sublime_plugin.TextCommand):
    def run(self, edit, args):
        self.view.insert(edit, self.view.sel()[0].begin(), args['text'])

