"""
                  ___.   .__  .__                            __
        ________ _\_ |__ |  | |__| _____   ____      _______|  | __
       /  ___/  |  \ __ \|  | |  |/     \_/ __ \     \___   /  |/ /
       \___ \|  |  / \_\ \  |_|  |  Y Y  \  ___/      /    /|    <
      /____  >____/|___  /____/__|__|_|  /\___  >    /_____ \__|_ \
           \/          \/              \/     \/           \/    \/
                                       The SublimeText Zettelkasten
"""
import sublime, sublime_plugin, os, re, subprocess, glob, datetime
from collections import defaultdict, deque
import threading
import io
from subprocess import Popen, PIPE
import struct
import imghdr
import unicodedata
from collections import Counter
from operator import itemgetter


class ZkConstants:
    """
    Some constants used over and over
    """
    Settings_File = 'sublime_zk.sublime-settings'
    Syntax_File = 'Packages/sublime_zk/sublime_zk.sublime-syntax'
    Link_Prefix = '['
    Link_Prefix_Len = len(Link_Prefix)
    Link_Postfix = ']'

    # characters at which a #tag is cut off (#tag, -> #tag)
    Tag_Stops = '.,\/!$%\^&\*;\{\}[]\'"=`~()<>\\'

    TAG_PREFIX = '#'

    # search for tags in files
    def RE_TAGS():
        prefix = re.escape(ZkConstants.TAG_PREFIX)
        return r"(?<=\s|^)(?<!`)(" + prefix + r"+([^" + prefix + r"\s.,\/!$%\^&\*;{}\[\]'\"=`~()<>”\\]|:[a-zA-Z0-9])+)"
    # Same RE just for ST python's re module
    ## un-require line-start, sublimetext python's RE doesn't like it
    def RE_TAGS_PY():
        prefix = re.escape(ZkConstants.TAG_PREFIX)
        return r"(?<=\s)(?<!`)(" + prefix + r"+([^" + prefix + r"\s.,\/!$%\^&\*;{}\[\]'\"=`~()<>”\\]|:[a-zA-Z0-9])+)"

    # match note links in text
    Link_Matcher = re.compile('(\[+|§)([0-9.]{12,18})(\]+|.?)')
    # Above RE doesn't really care about closing ] andymore
    # This works in our favour so we support [[201711122259 This is a note]]
    # when expanding overview notes

    # image links with attributes
    RE_IMG_LINKS = '(!\[)(.*)(\])(\()(.*)(\))(\{)(.*)(\})'

    # TOC markers
    TOC_HDR = '<!-- table of contents (auto) -->'
    TOC_END = '<!-- (end of auto-toc) -->'


class ZKMode:
    ZKM_Results_Syntax_File = 'Packages/sublime_zk/zk-mode/sublime_zk_results.sublime-syntax'
    ZKM_SavedSearches_Syntax_File = 'Packages/sublime_zk/zk-mode/sublime_zk_search.sublime-syntax'
    ZKM_SavedSearches_File = '.saved_searches.zks'

    @staticmethod
    def saved_searches_file(folder):
        """ Return the name of the external search results file. """
        return os.path.join(folder, ZKMode.ZKM_SavedSearches_File)

    @staticmethod
    def do_layout(window):
        window.run_command('set_layout', {
            'cols': [0.0, 0.7, 1.0],
            'rows': [0.0, 0.8, 1.0],
            'cells': [[0, 0, 1, 2], [1, 0, 2, 1], [1, 1, 2, 2]]
        })

    @staticmethod
    def enter(window):
        global PANE_FOR_OPENING_RESULTS
        global PANE_FOR_OPENING_NOTES

        PANE_FOR_OPENING_RESULTS = 1
        PANE_FOR_OPENING_NOTES = 0

        # check if we have a folder
        if window.project_file_name():
            folder = os.path.dirname(window.project_file_name())
        else:
            # no project. try to create one
            window.run_command('save_project_as')
            # if after that we still have no project (user canceled save as)
            folder = os.path.dirname(window.project_file_name())
            if not folder:
                # I don't know how to save_as the file so there's nothing sane I
                # can do here. Non-obtrusively warn the user that this failed
                window.status_message(
                'Zettelkasten mode cannot be entered without a project or an open folder!')
                return False
        ZKMode.do_layout(window)
        results_file = ExternalSearch.external_file(folder)
        lines = """# Welcome to Zettelkasten Mode!

 #!  ...  Show all Tags
 [!  ...  Show all notes
 #?  ...  Browse & insert tag
 [[  ...  Browse notes & insert link

shift + enter  ...  Create new note
ctrl  + enter  ...  Follow link under cursor and open note
ctrl  + enter  ...  Follow #tag or citekey under cursor and
                    show referencing notes
alt   + enter  ...  Show notes referencing link under cursor
ctrl  + .      ...  Create list of referencing notes in the
                    current note
                    (link, #tag, or citekey under cursor)
""".split('\n')
        if not os.path.exists(results_file):
            # create it
            with open(results_file, mode='w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
        # now open and show the file
        ExternalSearch.show_search_results(window, folder, 'Welcome', lines,
                                                'show_all_tags_in_new_pane')

        # now open saved searches
        searches_file = ZKMode.saved_searches_file(folder)
        if not os.path.exists(searches_file):
            with open(searches_file, mode='w', encoding='utf-8') as f:
                f.write("""# Saved Searches

All Notes:      [!
All Tags:       #!

## tag1 or tag2
Tag1orTag2:    #tag1 #tag2

## tag1 and not tag2
Complex:        #tag1, !#tag2
""")
        new_view = window.open_file(searches_file)
        window.set_view_index(new_view, 2, 0)
        new_view.set_syntax_file(ZKMode.ZKM_SavedSearches_Syntax_File)
        window.focus_group(PANE_FOR_OPENING_NOTES)
        window.set_sidebar_visible(False)



# global magic
F_EXT_SEARCH = False
PANE_FOR_OPENING_NOTES = 0
PANE_FOR_OPENING_RESULTS = 1
DISTRACTION_FREE_MODE_ACTIVE = defaultdict(bool)
VIEWS_WITH_IMAGES = set()
AUTO_SHOW_IMAGES = False
SECONDS_IN_ID = False


def get_settings():
    return sublime.load_settings(ZkConstants.Settings_File)

def settings_changed():
    global PANE_FOR_OPENING_RESULTS
    global PANE_FOR_OPENING_NOTES
    global AUTO_SHOW_IMAGES
    global SECONDS_IN_ID
    settings = get_settings()
    value = settings.get("pane_for_opening_notes", None)
    if value is not None:
        PANE_FOR_OPENING_NOTES = value
    value = settings.get("pane_for_opening_results", None)
    if value is not None:
        PANE_FOR_OPENING_RESULTS = value
    AUTO_SHOW_IMAGES = settings.get('auto_show_images', False)
    value = settings.get("seconds_in_id", None)
    if value is not None:
        SECONDS_IN_ID = value
    value = settings.get("tag_prefix", None)
    if value is not None:
        ZkConstants.TAG_PREFIX = value


def plugin_loaded():
    global F_EXT_SEARCH
    F_EXT_SEARCH = os.system(ExternalSearch.SEARCH_COMMAND + ' --help') == 0

    if F_EXT_SEARCH:
        print('Sublime_ZK: Using ag!')
    else:
        settings = get_settings()
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
    settings = get_settings()
    settings.clear_on_change("sublime_zk_notify")
    settings.add_on_change("sublime_zk_notify", settings_changed)
    settings_changed()


class TagSearch:
    """
    Advanced tag search.

    Grammar:
    ```
        search-spec: search-term [, search-term]*
        search-term: tag-spec [ tag-spec]*
        tag-spec: [!]#tag-name[*]
        tag-name: {any valid tag string}
    ```
    """

    @staticmethod
    def advanced_tag_search(search_spec, folder, extension):
        """
        Return ids of all notes matching the search_spec.
        """
        if search_spec.startswith('[!'):
            sublime.active_window().run_command('zk_show_all_notes')
            return
        elif search_spec.startswith('#!'):
            sublime.active_window().run_command('zk_show_all_tags')
            return
        note_tag_map = find_all_notes_all_tags_in(folder, extension)
        print('Note Tag Map for ', folder, extension)
        for k,v in note_tag_map.items():
            print('{} : {}'.format(k, v))
        for sterm in [s.strip() for s in search_spec.split(',')]:
            # iterate through all notes and apply the search-term
            sterm_results = {}
            for note_id, tags in note_tag_map.items():
                if not note_id:
                    continue
                # apply each tag-spec match to all tags
                for tspec in sterm.split():
                    if tspec[0] == '!':
                        if tspec[-1] == '*':
                            match = TagSearch.match_not_startswith(tspec, tags)
                        else:
                            match = TagSearch.match_not(tspec, tags)
                    else:
                        if tspec[-1] == '*':
                            match = TagSearch.match_startswith(tspec, tags)
                        else:
                            match = TagSearch.match_tag(tspec, tags)
                    if match:
                        sterm_results[note_id] = tags   # remember this note
            # use the results for the next search-term
            note_tag_map = sterm_results
        result = list(sterm_results.keys())
        result.sort()
        return result

    @staticmethod
    def match_not(tspec, tags):
        return tspec[1:] not in tags

    @staticmethod
    def match_tag(tspec, tags):
        return tspec in tags

    @staticmethod
    def match_not_startswith(tspec, tags):
        tspec = tspec[1:-1]
        return len([t for t in tags if t.startswith(tspec)]) == 0

    @staticmethod
    def match_startswith(tspec, tags):
        tspec = tspec[:-1]
        return [t for t in tags if t.startswith(tspec)]


class ImageHandler:
    """
    Static class to bundle image handling.
    """

    FMT = '''
        <img src="file://{}" class="centerImage" {}>
    '''
    Phantoms = defaultdict(set)

    @staticmethod
    def show_images(view, edit, max_width=1024):
        """
        markup.underline.link.image.markdown
        """
        global DISTRACTION_FREE_MODE_ACTIVE
        global VIEWS_WITH_IMAGES
        folder = get_path_for(view)
        if not folder:
            return
        skip = 0
        while True:
            img_regs = view.find_by_selector(
                'markup.underline.link.image.markdown')[skip:]
            skip += 1
            if not img_regs:
                break
            region = img_regs[0]
            rel_p = view.substr(region)
            if rel_p.startswith('http'):
                continue

            img = os.path.join(folder, rel_p)
            size  = ImageHandler.get_image_size(img)
            if not size:
                continue
            w, h = size
            line_region = view.line(region)
            imgattr = ImageHandler.check_imgattr(view, line_region, region)
            if not imgattr:
                if w > max_width:
                    m = max_width / w
                    h *= m
                    w = max_width
                imgattr = 'width="{}" height="{}"'.format(w, h)

            settings = sublime.load_settings(
                'Distraction Free.sublime-settings')
            spaces = settings.get('wrap_width', 80)
            centered = settings.get('draw_centered', True)
            view.erase_phantoms(str(region))
            html_img = ImageHandler.FMT.format(img, imgattr)
            if centered and DISTRACTION_FREE_MODE_ACTIVE[view.window().id()]:
                line_str = view.substr(line_region)
                line_len = len(line_str)
                spaces -= line_len + 1
                view.insert(edit, region.b, ' ' * spaces)
                view.add_phantom(
                    str(region),
                    sublime.Region(line_region.b + spaces,
                                   line_region.b + spaces),
                    html_img,
                    sublime.LAYOUT_BELOW)
            else:
                view.add_phantom(str(region), region,
                                 html_img,
                                 sublime.LAYOUT_BLOCK)
            ImageHandler.Phantoms[view.id()].add(str(region))
        VIEWS_WITH_IMAGES.add(view.id())

    @staticmethod
    def check_imgattr(view, line_region, link_region=None):
        # find attrs for this link
        full_line = view.substr(line_region)
        link_till_eol = full_line[link_region.a - line_region.a:]
        # find attr if present
        m = re.match(r'.*\)\{(.*)\}', link_till_eol)
        if m:
            return m.groups()[0]


    @staticmethod
    def hide_images(view, edit):
        """
        Hide all imgs; use buffered identifiers
        """
        for rel_p in ImageHandler.Phantoms[view.id()]:
            view.erase_phantoms(rel_p)
        del ImageHandler.Phantoms[view.id()]
        skip = 0
        while True:
            img_regs = view.find_by_selector(
                'markup.underline.link.image.markdown')[skip:]
            skip += 1
            if not img_regs:
                break
            region = img_regs[0]
            rel_p = view.substr(region)
            line_region = view.line(region)
            line_str = view.substr(line_region)
            view.replace(edit, line_region, line_str.strip())
        VIEWS_WITH_IMAGES.discard(view.id())

    @staticmethod
    def get_image_size(img):
        """
        Determine the image type of img and return its size.
        """
        with open(img, 'rb') as f:
            head = f.read(24)
            # print('head:\n', repr(head))
            if len(head) != 24:
                return
            if imghdr.what(img) == 'png':
                check = struct.unpack('>i', head[4:8])[0]
                if check != 0x0d0a1a0a:
                    return
                width, height = struct.unpack('>ii', head[16:24])
            elif imghdr.what(img) == 'gif':
                width, height = struct.unpack('<HH', head[6:10])
            elif imghdr.what(img) == 'jpeg':
                try:
                    f.seek(0) # Read 0xff next
                    size = 2
                    ftype = 0
                    while not 0xc0 <= ftype <= 0xcf:
                        f.seek(size, 1)
                        byte = f.read(1)
                        while ord(byte) == 0xff:
                            byte = f.read(1)
                        ftype = ord(byte)
                        size = struct.unpack('>H', f.read(2))[0] - 2
                    # SOFn block
                    f.seek(1, 1)  # skip precision byte.
                    height, width = struct.unpack('>HH', f.read(4))
                except Exception:
                    return
            else:
                return
            return width, height

class Autobib:
    """
    Static class to group all auto-bibliography functions.
    """
    citekey_matcher = re.compile('^@.*{([^,]*)[,]?')
    author_matcher = re.compile(r'^\s*author\s*=\s*(.*)', re.IGNORECASE)
    title_matcher = re.compile(r'^\s*title\s*=\s*(.*)', re.IGNORECASE)
    year_matcher = re.compile(r'^\s*year\s*=\s*(.*)', re.IGNORECASE)

    @staticmethod
    def look_for_bibfile(view, settings):
        """
        Look for a bib file in the view's folder.
        If no bib file there, then query the setting.
        """
        folder = get_path_for(view)
        if folder:
            pattern = os.path.join(folder, '*.bib')
            bibs = glob.glob(pattern)
            if bibs:
                print('Using local', bibs[0])
                return bibs[0]
        # try the setting
        bibfile = settings.get('bibfile', None)
        if bibfile:
            if os.path.exists(bibfile):
                print('Using global', bibfile)
                return bibfile
            else:
                print('bibfile not found:', bibfile)
                return None

    @staticmethod
    def extract_all_citekeys(bibfile):
        """
        Parse the bibfile and return all citekeys.
        """
        citekeys = set()
        if not os.path.exists(bibfile):
            print('bibfile not found:', bibfile)
            return []
        with open(bibfile, mode='r', encoding='utf-8') as f:
            for line in f:
                match = Autobib.citekey_matcher.findall(line)
                if not match:
                    continue
                citekeys.add(match[0])
        return citekeys

    @staticmethod
    def extract_all_entries(bibfile):
        """
        Return dict: {citekey: {title, authors, year}}
        """
        entries = defaultdict(lambda: defaultdict(str))
        if not os.path.exists(bibfile):
            print('bibfile not found:', bibfile)
            return {}
        current_citekey = None
        with open(bibfile, mode='r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.endswith(','):
                    line = line[:-1]
                match = Autobib.citekey_matcher.findall(line)
                if match:
                    current_citekey = match[0]
                    continue
                match = Autobib.author_matcher.findall(line)
                if match:
                    authors = match[0]
                    authors = Autobib.parse_authors(authors)
                    entries[current_citekey]['authors'] = authors
                    continue
                match = Autobib.title_matcher.findall(line)
                if match:
                    title = match[0]
                    title = Autobib.remove_latex_commands(title)
                    entries[current_citekey]['title'] = title
                    continue
                match = Autobib.year_matcher.findall(line)
                if match:
                    year = match[0]
                    year = Autobib.remove_latex_commands(year)
                    entries[current_citekey]['year'] = year
                    continue
        return entries

    @staticmethod
    def parse_authors(line):
        line = Autobib.remove_latex_commands(line)
        authors = line.split(' and')
        author_tuples = []
        for author in authors:
            first = ''
            last = author.strip()
            if ',' in author:
                last, first = [x.strip() for x in author.split(',')][:2]
            author_tuples.append((last, first))
        if len(author_tuples) > 2:
            authors = '{} et al.'.format(author_tuples[0][0])  # last et al
        else:
            authors = ' & '.join(x[0] for x in author_tuples)
        return authors

    @staticmethod
    def remove_latex_commands(s):
        """
        Simple function to remove any LaTeX commands or brackets from the string,
        replacing it with its contents.
        """
        chars = []
        FOUND_SLASH = False

        for c in s:
            if c == '{':
                # i.e., we are entering the contents of the command
                if FOUND_SLASH:
                    FOUND_SLASH = False
            elif c == '}':
                pass
            elif c == '\\':
                FOUND_SLASH = True
            elif not FOUND_SLASH:
                chars.append(c)
            elif c.isspace():
                FOUND_SLASH = False

        return ''.join(chars)

    @staticmethod
    def find_citations(text, citekeys):
        """
        Find all mentioned citekeys in text
        """
        citekey_stops = r"[@',\#}{~%\[\]\s]"
        citekeys_re = [re.escape('@' + citekey) for citekey in citekeys]
        citekeys_re.extend([re.escape('[#' + citekey) for citekey in citekeys])
        citekeys_re = [ckre + citekey_stops for ckre in citekeys_re]
        # print('\n'.join(citekeys_re))
        finder = re.compile('|'.join(citekeys_re))
        founds_raw = finder.findall(text)
        founds = []
        for citekey in founds_raw:
            if citekey.startswith('[#'):
                citekey = citekey[1:]
            founds.append(citekey[:-1])   # don't add stop char
        founds = set(founds)
        return founds

    @staticmethod
    def create_bibliography(text, bibfile, pandoc='pandoc'):
        """
        Create a bibliography for all citations in text in form of a dictionary.
        """
        citekeys = Autobib.extract_all_citekeys(bibfile)
        if not citekeys:
            return {}
        citekeys = Autobib.find_citations(text, citekeys)
        citekey2bib = {}
        for citekey in citekeys:
            pandoc_input = citekey.replace('#', '@', 1)
            pandoc_out = Autobib.run(pandoc, bibfile, pandoc_input)
            citation, bib = Autobib.parse_pandoc_out(pandoc_out)
            citekey2bib[citekey] = bib
        return citekey2bib

    @staticmethod
    def parse_pandoc_out(pandoc_out):
        """
        Splits pandoc output into citation and bib part
        """
        # print('pandoc_out:', repr(pandoc_out))
        pdsplit = pandoc_out.split('\n\n')
        citation = '(no citation generated)'
        bib =  '(no bib generated)'
        if len(pdsplit) >= 1:
            citation = pdsplit[0]
        if len(pdsplit) >= 2:
            bib = pdsplit[1]
        citation = citation.replace('\n', ' ')
        bib = bib.replace('\n', ' ')
        return citation, bib

    @staticmethod
    def run(pandoc_bin, bibfile, stdin):
        args = [pandoc_bin, '-t', 'plain', '--bibliography', bibfile]
        # using universal_newlines here gets us into decoding troubles as the
        # encoding then is guessed and can be ascii which can't deal with
        # unicode characters. hence, we handle \r ourselves
        p = Popen(args, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        stdout, stderr = p.communicate(bytes(stdin, 'utf-8'))
        # make me windows-safe
        stdout = stdout.decode('utf-8', errors='ignore').replace('\r', '')
        stderr = stderr.decode('utf-8', errors='ignore').replace('\r', '')
        # print('pandoc says:', stderr)
        return stdout


class ExternalSearch:
    """
    Static class to group all external search related functions.
    """
    SEARCH_COMMAND = 'ag'
    EXTERNALIZE = '.search_results.zkr'   # '' to skip

    @staticmethod
    def search_all_tags(folder, extension):
        """
        Create a list of all #tags of all notes in folder.
        """
        output = ExternalSearch.search_in(folder, ZkConstants.RE_TAGS(),
            extension, tags=True)
        tags = set()
        for line in output.split('\n'):
            if line:
                tags.add(line)
        if ExternalSearch.EXTERNALIZE:
            with open(ExternalSearch.external_file(folder), mode='w',
                    encoding='utf-8') as f:
                f.write('# All Tags\n\n')
                for tag in sorted(tags):
                    f.write(u'* {}\n'.format(tag))
        return list(tags)

    @staticmethod
    def notes_and_tags_in(folder, extension):
        """
        Return a dict {note_id: tags}.
        """
        args = [ExternalSearch.SEARCH_COMMAND, '--nocolor', '--ackmate']
        args.extend(['--nonumbers', '-o', '--silent', '-G', '.*\\' + extension,
            ZkConstants.RE_TAGS(), folder])
        ag_out = ExternalSearch.run(args, folder)
        if not ag_out:
            return {}
        note_tags = defaultdict(list)
        note_id = None

        lines = deque(ag_out.split('\n'))
        lindex = 0
        num_lines = len(lines)

        while lines:
            line = lines.popleft()
            if not line.startswith(':'):
                continue
            note_id = get_note_id_of_file(line[1:])
            line = lines.popleft()
            while line: # until newline
                # parse findspec
                positions, txt_line = line.split(':', 1)
                for position in positions.split(','):
                    start, width = position.split()
                    start = int(start)
                    width = int(width)
                    tag = txt_line[start:start+width]
                    note_tags[note_id].append(tag.strip())
                line = lines.popleft()
        return note_tags

    @staticmethod
    def search_tagged_notes(folder, extension, tag, externalize=True):
        """
        Return a list of note files containing #tag.
        """
        output = ExternalSearch.search_in(folder, tag, extension)
        prefix = 'Notes referencing {}:'.format(tag)
        if externalize:
            ExternalSearch.externalize_note_links(output, folder, extension, prefix)
        return output.split('\n')

    @staticmethod
    def search_friend_notes(folder, extension, note_id):
        """
        Return a list of notes referencing note_id.
        """
        regexp = '(\[' + note_id + ')|(§' + note_id + ')'   # don't insist on ]
        output = ExternalSearch.search_in(folder, regexp, extension)
        link_prefix, link_postfix = get_link_pre_postfix()
        prefix = 'Notes referencing {}{}{}:'.format(link_prefix, note_id,
            link_postfix)
        ExternalSearch.externalize_note_links(output, folder, extension,
            prefix)
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
            args.extend(['--nofilename', '--nonumbers', '--only-matching'])
        else:
            args.extend(['-l', '--ackmate'])
        args.extend(['--silent', '-G', '.*\\' + extension, regexp, folder])
        return ExternalSearch.run(args, folder)

    @staticmethod
    def run(args, folder):
        """
        Execute ag to run a search, handle errors & timeouts.
        Return output of stdout as string.
        """
        output = b''
        verbose = False
        if verbose or True:
            print('cmd:', ' '.join(args))
        try:
            output = subprocess.check_output(args, shell=False, timeout=10000)
        except subprocess.CalledProcessError as e:
            print('sublime_zk: search unsuccessful:')
            print(e.returncode)
            print(e.cmd)
            for line in e.output.decode('utf-8', errors='ignore').split('\n'):
                print('    ', line)
        except subprocess.TimeoutExpired:
            print('sublime_zk: search timed out:', ' '.join(args))
        if verbose:
            print(output.decode('utf-8', errors='ignore'))
        return output.decode('utf-8', errors='ignore').replace('\r', '')

    @staticmethod
    def externalize_note_links(ag_out, folder, extension, prefix=None):
        """
        If enabled, write ag file name output into external search results file
        in `[[note_id]] note title` style.
        """
        if ExternalSearch.EXTERNALIZE:
            link_prefix, link_postfix = get_link_pre_postfix()
            with open(ExternalSearch.external_file(folder),
                mode='w', encoding='utf-8') as f:
                if prefix:
                    f.write(u'{}\n\n'.format(prefix))
                results = []
                for line in sorted(ag_out.split('\n')):
                    if not line.strip():
                        continue
                    if line.endswith(extension):
                        line = os.path.basename(line)
                        line = line.replace(extension, '')
                        if not ' ' in line:
                            line += ' '
                        note_id, title = line.split(' ', 1)
                        note_id = os.path.basename(note_id)
                        results.append((note_id, title))
                settings = get_settings()
                sort_order = settings.get('sort_notelists_by', 'id').lower()
                if sort_order not in ('id', 'title'):
                    sort_order = 'id'
                column = 0
                if sort_order == 'title':
                    column = 1
                results.sort(key=itemgetter(column))
                for note_id, title in results:
                        f.write(u'* {}{}{} {}\n'.format(link_prefix, note_id,
                            link_postfix, title))

    @staticmethod
    def external_file(folder):
        """ Return the name of the external search results file. """
        return os.path.join(folder, ExternalSearch.EXTERNALIZE)

    @staticmethod
    def show_search_results(window, folder, title, lines, new_pane_setting):
        """
        Helper method to display the results either in the external file
        or, if not available, in a new window
        """
        global PANE_FOR_OPENING_RESULTS
        if ExternalSearch.EXTERNALIZE:
            new_view = window.open_file(ExternalSearch.external_file(folder))
            window.set_view_index(new_view, PANE_FOR_OPENING_RESULTS, 0)
            new_view.set_syntax_file(ZKMode.ZKM_Results_Syntax_File)
        else:
            settings = get_settings()
            new_pane = settings.get(new_pane_setting)
            if new_pane:
                window.run_command('set_layout', {
                    'cols': [0.0, 0.5, 1.0],
                    'rows': [0.0, 1.0],
                    'cells': [[0, 0, 1, 1], [1, 0, 2, 1]]
                })
                # goto right-hand pane
                window.focus_group(1)
            tagview = window.new_file()
            tagview.set_name(title)
            tagview.set_scratch(True)
            tagview.run_command("insert",{"characters": ' ' + '\n'.join(lines)})
            tagview.set_syntax_file(ZkConstants.Syntax_File)
            # return back to note
            window.focus_group(0)


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
    def embed_note(note_id, folder, extension, link_prefix, link_postfix):
        """
        Put the contents of a note into a comment block.
        """
        result_lines = []
        note_file, content = TextProduction.read_full_note(note_id, folder,
                                                                    extension)
        footer = '<!-- (End of note ' + note_id + ') -->'
        if not content:
            header = '<!-- Note not found: ' + note_id + ' -->'
            result_lines.append(header)
        else:
            filename = os.path.basename(note_file).replace(extension, '')
            filename = filename.split(' ', 1)[1]
            header = link_prefix + note_id + link_postfix + ' ' + filename
            header = '<!-- !    ' + header + '    -->'
            result_lines.append(header)
            result_lines.extend(content.split('\n'))
            result_lines.append(footer)
        return result_lines

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
                    result_lines.extend(TextProduction.embed_note(note_id,
                        folder, extension, pre, post))
            else:
                result_lines.append(line)
        return '\n'.join(result_lines)

    @staticmethod
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
                result_lines.extend(TextProduction.embed_note(note_id, folder,
                    extension, pre, post))
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

    @staticmethod
    def expand_link_in(view, edit, folder, extension):
        """
        Expand note-link under cursor inside the current view
        """
        linestart_till_cursor_str, link_region = select_link_in(view)
        cursor_pos = view.sel()[0].begin()
        line_region = view.line(cursor_pos)
        pre, post = get_link_pre_postfix()
        link_text = ''
        if link_region:
            link_text = view.substr(link_region)
        link_is_citekey = link_text.startswith('@') or link_text.startswith('#')

        if link_region and not link_is_citekey:
            # we're in a link, so expand it
            note_id = cut_after_note_id(view.substr(link_region))
            result_lines = TextProduction.embed_note(note_id, folder, extension,
                                                                      pre, post)
            result_lines.append('')   # append a newline for empty line after exp.
            view.insert(edit, line_region.b, '\n' + '\n'.join(result_lines))
        else:
            if link_is_citekey:
                tag = link_text
            else:
                # check if we're in a tag
                full_line = view.substr(line_region)
                line_start = line_region.begin()
                cursor_pos_in_line = cursor_pos - line_start
                tag, (begin, end) = tag_at(full_line, cursor_pos_in_line)
                if not tag:
                    tag, (begin, end) = pandoc_citekey_at(full_line,
                        cursor_pos_in_line)
                    if not tag:
                        return
            # we have a #tag so let's search for tagged notes
            note_list = ExternalSearch.search_tagged_notes(folder, extension,
                tag, externalize=False)
            bullet_list = []
            results = []
            for line in sorted(note_list):
                if not line:
                    continue
                if line.endswith(extension):
                    line = os.path.basename(line)
                    line = line.replace(extension, '')
                    note_id, title = line.split(' ', 1)
                    note_id = os.path.basename(note_id)
                    results.append((note_id, title))
            settings = get_settings()
            sort_order = settings.get('sort_notelists_by', 'id').lower()
            if sort_order not in ('id', 'title'):
                sort_order = 'id'
            column = 0
            if sort_order == 'title':
                column = 1
            results.sort(key=itemgetter(column))
            for note_id, title in results:
                bullet_line = '* {}{}{} {}'.format(pre, note_id, post, title)
                bullet_list.append(bullet_line)
            view.insert(edit, line_region.b, '\n' + '\n'.join(bullet_list))



def timestamp():
    global SECONDS_IN_ID
    if SECONDS_IN_ID:
        return '{:%Y%m%d%H%M%S}'.format(datetime.datetime.now())
    else:
        return '{:%Y%m%d%H%M}'.format(datetime.datetime.now())

def cut_after_note_id(text):
    """
    Tries to find the 12/14 digit note ID (at beginning) in text.
    """
    note_ids = re.findall('[0-9.]{12,18}', text)
    if note_ids:
        return note_ids[0]

def get_link_pre_postfix():
    settings = get_settings()
    extension = settings.get('wiki_extension')
    link_prefix = '[['
    link_postfix = ']]'
    if not settings.get('double_brackets', True):
        link_prefix = '['
        link_postfix = ']'
    return link_prefix, link_postfix

def note_template_handle_date_spec(template, note_id):
    global SECONDS_IN_ID
    try:
        if SECONDS_IN_ID:
            timestamp = datetime.datetime.strptime(note_id, '%Y%m%d%H%M%S')
        else:
            timestamp = datetime.datetime.strptime(note_id, '%Y%m%d%H%M')
    except ValueError:
        return template

    # now handle the format string(s)
    new_template = template
    for pre, fmt, post in re.findall('({timestamp:\s*)([^\}]*)(})', template):
        spec = pre + fmt + post
        new_template = new_template.replace(spec, timestamp.strftime(fmt))

    return new_template

def create_note(filn, title, origin_id=None, origin_title=None, body=None):
    note_id = os.path.basename(filn).split()[0]
    params = {
                'title': title,
                'file': os.path.basename(filn),
                'path': os.path.dirname(filn),
                'id': note_id,
                'origin_id': origin_id,
                'origin_title': origin_title,
                # don't break legacy
                'origin': origin_id,
              }
    settings = get_settings()
    format_str = settings.get('new_note_template')
    if not format_str:
        format_str = u'# {title}\ntags = \n\n'
    else:
        format_str = note_template_handle_date_spec(format_str, note_id)
    with open(filn, mode='w', encoding='utf-8') as f:
        f.write(format_str.format(**params))
        if body is not None:
            f.write('\n' + body)

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

    if folder is None:
        print('sublime_zk: could not deduce your note archive folder!')
        view.window().status_message('Could not find the location of your note '
            'archive! See the README for how to create a project!')
    return folder

def note_file_by_id(note_id, folder, extension):
    """
    Find the file for note_id.
    """
    if not note_id:
        return
    candidates = []
    for root, dirs, files in os.walk(folder):
        candidates.extend([os.path.join(root, f) for f in files if f.startswith(note_id)])
    if len(candidates) > 0:
        return candidates[0]

def extract_tags(file):
    """
    Extract #tags from file.
    Returns all words starting with `#`.
    To be precise, it returns everything that matches RE_TAGS_PY.
    """
    tags = set()
    with open(file, mode='r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            for tag in re.findall(ZkConstants.RE_TAGS_PY(), line):
                tags.add(tag[0])
    return tags

def get_all_notes_for(folder, extension):
    """
    Return all files with extension in folder.
    """
    candidates = []
    for root, dirs, files in os.walk(folder):
        candidates.extend([os.path.join(root, f) for f in files if f.endswith(extension)])
    return candidates

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

def find_all_notes_all_tags_in(folder, extension):
    """
    Manual and ag implementation to get a dict mapping note_ids to tags
    """
    global F_EXT_SEARCH
    if F_EXT_SEARCH:
        return ExternalSearch.notes_and_tags_in(folder, extension)
    # manual implementation
    ret = {}
    for filn in get_all_notes_for(folder, extension):
        note_id = get_note_id_of_file(filn)
        if not note_id:
            continue
        tags = list(extract_tags(filn))
        if tags:
            ret[note_id] = tags
    return ret

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
    inner = search_text.rfind(ZkConstants.TAG_PREFIX)
    if inner >=0:
        # find next consecutive `#`
        for c in reversed(search_text[:inner]):
            if c not in ZkConstants.TAG_PREFIX:
                break
            inner -=1
        # search end of tag
        end = inner
        mode = ''
        for c in text[inner:]:
            if mode == ':':
                if (c >= 'a' and c <= 'z') or (c >= 'A' and c <= 'Z') \
                    or (c >= '0' and c <= '9'):
                    pass
                else:
                    end -= 1
                    break
            if c.isspace() or c in ZkConstants.Tag_Stops:
                break
            mode = c
            end += 1
        tag = text[inner:end]
        if tag.endswith(':'):
            tag = tag[:-1]
            end -= 1

        # test if it's just a `# heading` (resulting in `#`) or a real tag
        if tag.replace('#', ''):
            return text[inner:end], (inner, end)
    return '', (None, None)

def pandoc_citekey_at(text, pos=None):
    """
    Search for a ####tag inside of text.
    If pos is given, searches for the tag at pos
    """
    if pos is None:
        search_text = text
    else:
        search_text = text[:pos + 1]
    # find first `#`
    inner = search_text.rfind('@')
    if inner >=0:
        # find next consecutive `#`
        for c in reversed(search_text[:inner]):
            if c != '@':
                break
            inner -=1
        # search end of tag
        end = inner
        mode = ''
        for c in text[inner:]:
            if mode == ':':
                if (c >= 'a' and c <= 'z') or (c >= 'A' and c <= 'Z') \
                    or (c >= '0' and c <= '9'):
                    pass
                else:
                    end -= 1
                    break
            if c.isspace() or c in ZkConstants.Tag_Stops:
                break
            mode = c
            end += 1
        tag = text[inner:end]
        if tag.endswith(':'):
            tag = tag[:-1]
            end -= 1

        # test if it's just a `# heading` (resulting in `#`) or a real tag
        if tag.replace('@', ''):
            return text[inner:end], (inner, end)
    return '', (None, None)

def select_link_in(view, event=None):
    """
    Used by different commands to select the link under the cursor, if
    any.
    Return the
    """
    if event is None:
        region = view.sel()[0]
        cursor_pos = region.begin()
    else:
        cursor_pos = view.window_to_text((event['x'], event['y']))

    line_region = view.line(cursor_pos)
    line_start = line_region.begin()

    linestart_till_cursor_str = view.substr(sublime.Region(line_start,
        cursor_pos))
    full_line = view.substr(line_region)

    # hack for § links
    p_symbol_pos = linestart_till_cursor_str.rfind('§')
    if p_symbol_pos >= 0:
        p_link_start = line_start + p_symbol_pos + 1
        note_id = cut_after_note_id(full_line[p_symbol_pos:])
        if note_id:
            p_link_end = p_link_start + len(note_id)
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

def get_note_id_of_file(filn):
    """
    Return the note id of the file named filn or None.
    """
    settings = get_settings()
    extension = settings.get('wiki_extension')
    if filn.endswith(extension):
        # we have a markdown file
        note_id = cut_after_note_id(os.path.basename(filn))
        if note_id:
            if os.path.basename(filn).startswith(note_id):
                return note_id

def get_note_id_and_title_of(view):
    """
    Return the note id  and title of the given view.
    """
    filn = view.file_name()
    origin_id = None
    origin_title = None
    if filn:
        origin_id = get_note_id_of_file(filn)
        origin_title = ''
        if origin_id:
            # split off title and replace extension
            origin_title = filn.rsplit(origin_id)[1].strip().rsplit('.')[0]
    return origin_id, origin_title


def post_open_note(view, pane):
    """
    New view has been created for a note. Move it to the destination pane.
    If the pane is -1, create a new pane to the right and move the view
    there
    """

    def increment_if_greater_or_equal(x, threshold):
        if x >= threshold:
            return x+1
        return x


    def push_right_cells_after(cells, threshold):
        return [    [increment_if_greater_or_equal(x0, threshold),y0,
                    increment_if_greater_or_equal(x1, threshold),y1] for (x0,y0,x1,y1) in cells]

    if pane > -1:
        view.window().set_view_index(view, pane, 0)  #,0..make it first view
    else:
        window = view.window()
        layout = window.get_layout()
        cells = layout["cells"]
        rows = layout["rows"]
        cols = layout["cols"]

        print('layout before', layout)
        num_groups = len(layout['cells'])
        current_group = num_groups - 1 # window.active_group()
        old_cell = cells.pop(current_group)
        new_cell = []

        XMIN, YMIN, XMAX, YMAX = list(range(4))
        cells = push_right_cells_after(cells, old_cell[XMAX])
        cols.insert(old_cell[XMAX], 1 / (num_groups + 1))
        new_cell = [old_cell[XMAX], old_cell[YMIN], old_cell[XMAX]+1, old_cell[YMAX]]
        old_cell = [old_cell[XMIN], old_cell[YMIN], old_cell[XMAX], old_cell[YMAX]]

        num_cols = len(cols)
        if num_cols > 2:
            new_cols = [0.0]
            delta = 1 / (num_cols)     # num_cols -1 for an even split
            start = 0.0
            for col in cols[1:-1]:
                start += delta
                new_cols.append(start)
            new_cols.append(1.0)
            cols = new_cols

        print('new cell', new_cell)
        print('old cell', old_cell)

        focused_cell = old_cell
        unfocused_cell = new_cell
        cells.insert(current_group, focused_cell)
        cells.append(unfocused_cell)
        layout = {"cols": cols, "rows": rows, "cells": cells}
        window.run_command('set_layout', layout)
        num_groups = len(layout['cells'])
        window.focus_group(min(current_group, num_groups-1))
        window.set_view_index(view, num_groups-1, 0)  #,0..make it first view
        print(window.get_layout())



class ZkExpandLinkCommand(sublime_plugin.TextCommand):
    """
    Command for expanding overview notes.
    """
    def run(self, edit):
        folder = get_path_for(self.view)
        if not folder:
            return

        settings = get_settings()
        extension = settings.get('wiki_extension')
        TextProduction.expand_link_in(self.view, edit, folder, extension)


class ZkExpandOverviewNoteCommand(sublime_plugin.TextCommand):
    """
    Command for expanding overview notes.
    """
    def run(self, edit):
        folder = get_path_for(self.view)
        if not folder:
            return

        settings = get_settings()
        extension = settings.get('wiki_extension')

        complete_text = self.view.substr(sublime.Region(0, self.view.size()))
        result_text = TextProduction.expand_links(complete_text, folder,
            extension, replace_lines=True)
        new_view = self.view.window().new_file()

        # don't: this causes auto-indent:
        # new_view.run_command("insert", {"characters": result_text})
        new_view.insert(edit, 0, result_text)   # no auto-indent
        # set syntax late, seems to speed insertion up
        new_view.set_syntax_file(ZkConstants.Syntax_File)


class ZkRefreshExpandedNoteCommand(sublime_plugin.TextCommand):
    """
    Command for refreshing expanded overview notes.
    """
    def run(self, edit):
        folder = get_path_for(self.view)
        if not folder:
            return

        settings = get_settings()
        extension = settings.get('wiki_extension')
        complete_region = sublime.Region(0, self.view.size())
        complete_text = self.view.substr(complete_region)
        result_text = TextProduction.refresh_result(complete_text, folder,
            extension)
        self.view.replace(edit, complete_region, result_text)


class ZkFollowWikiLinkCommand(sublime_plugin.TextCommand):
    """
    Command that opens the note corresponding to a link the cursor is placed in
    or searches for the tag under the cursor.
    """
    def on_done(self, selection):
        """
        Called when the link was a tag, a tag picker overlay was displayed, and
        a tag was selected by the user.
        """
        global PANE_FOR_OPENING_NOTES
        if selection == -1:
            return
        the_file = os.path.join(self.folder, self.tagged_note_files[selection])
        new_view = self.view.window().open_file(the_file)
        post_open_note(new_view, PANE_FOR_OPENING_NOTES)

    def select_link(self, event=None):
        """
        Select a note-link under the cursor.
        If it's a tag, follow it by searching for tagged notes.
        Search:
        * via find-in-files if ag is not found
        * results in external search results file if enabled
        * else present overlay to pick a note
        """
        global F_EXT_SEARCH
        global PANE_FOR_OPENING_RESULTS
        linestart_till_cursor_str, link_region = select_link_in(self.view, event)
        link_text = ''
        link_is_citekey = False
        if link_region:
            link_text = self.view.substr(link_region)
            link_is_citekey = link_text.startswith('@') or link_text.startswith('#')
            if not link_is_citekey:
                return link_region

        # test if we are supposed to follow a tag
        if ZkConstants.TAG_PREFIX in linestart_till_cursor_str or '@' in linestart_till_cursor_str:
            view = self.view
            if event is None:
                region = self.view.sel()[0]
                cursor_pos = region.begin()
            else:
                cursor_pos = self.view.window_to_text((event['x'], event['y']))
            line_region = view.line(cursor_pos)
            line_start = line_region.begin()
            full_line = view.substr(line_region)
            cursor_pos_in_line = cursor_pos - line_start
            tag, (begin, end) = tag_at(full_line, cursor_pos_in_line)
            if not tag:
                tag, (begin, end) = pandoc_citekey_at(full_line, cursor_pos_in_line)
                if not tag:
                    return


            settings = get_settings()

            if F_EXT_SEARCH:
                extension = settings.get('wiki_extension')
                folder = get_path_for(self.view)
                if not folder:
                    return
                self.folder = folder
                self.tagged_note_files = ExternalSearch.search_tagged_notes(
                    folder, extension, tag)
                if ExternalSearch.EXTERNALIZE:
                    n=self.view.window().open_file(ExternalSearch.external_file(
                        folder))
                    self.view.window().set_view_index(n,
                        PANE_FOR_OPENING_RESULTS, 0)

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
                selection.add(line_region)
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
        global PANE_FOR_OPENING_NOTES
        folder = get_path_for(self.view)
        if not folder:
            return

        settings = get_settings()
        extension = settings.get('wiki_extension')
        id_in_title = settings.get('id_in_title')

        print('EVENT', event)
        if event is None:
            region = self.view.sel()[0]
            cursor_pos = region.begin()
        else:
            cursor_pos = self.view.window_to_text((event['x'], event['y']))
            print('cursor pos', cursor_pos)
        # FIRST check if it's a saved search!!!
        if self.view.match_selector(cursor_pos, 'markup.zettel.search'):
                line_region = self.view.line(cursor_pos)
                line = self.view.substr(line_region)
                search_spec = line.split(':', 1)[1].strip()
                print('search_spec >' + search_spec + '<')
                self.folder = folder
                self.extension = extension
                input_text = search_spec
                self.window = self.view.window()
                #
                # VERBATIM FROM ZkMultiTagSearchCommand.on_done:
                #
                note_ids = TagSearch.advanced_tag_search(input_text, self.folder,
                    self.extension)
                print('note_ids', note_ids)
                if note_ids is None:
                    return
                link_prefix, link_postfix = get_link_pre_postfix()
                lines = ['# Notes matching search-spec ' + input_text + '\n']
                results = []
                for note_id in [n for n in note_ids if n]:  # Strip the None
                    filn = note_file_by_id(note_id, self.folder, self.extension)
                    if filn:
                        title = os.path.basename(filn).split(' ', 1)[1]
                        title = title.replace(self.extension, '')
                        results.append((note_id, title))
                settings = get_settings()
                sort_order = settings.get('sort_notelists_by', 'id').lower()
                if sort_order not in ('id', 'title'):
                    sort_order = 'id'
                column = 0
                if sort_order == 'title':
                    column = 1
                results.sort(key=itemgetter(column))
                for note_id, title in results:
                    line = '* ' + link_prefix + note_id + link_postfix + ' '
                    line += title
                    lines.append(line)
                if ExternalSearch.EXTERNALIZE:
                    with open(ExternalSearch.external_file(self.folder), mode='w',
                        encoding='utf-8') as f:
                        f.write('\n'.join(lines))
                ExternalSearch.show_search_results(self.window, self.folder,
                    'Tag-Search', lines, 'show_all_tags_in_new_pane')
                # END OF VERBATIM
                return


        window = self.view.window()
        location = self.select_link(event)

        if location is None:
            # no link found, not between brackets
            return

        selected_text = self.view.substr(location)

        # search for file starting with text between the brackets (usually
        # the ID)
        note_id = cut_after_note_id(selected_text)
        the_file = note_file_by_id(note_id, folder, extension)

        if the_file:
            new_view = window.open_file(the_file)
            post_open_note(new_view, PANE_FOR_OPENING_NOTES)
            new_view.set_syntax_file(ZkConstants.Syntax_File)
        else:
            # suppose you have entered "[[my new note]]", then we are going to
            # create "201710201631 my new note.md". We will also add a link
            # "[[201710201631]]" into the current document
            new_id = timestamp()
            the_file = new_id + ' ' + selected_text + extension
            the_file = os.path.join(folder, the_file)

            replace_str = new_id
            do_insert_title = settings.get('insert_links_with_titles', False)
            if do_insert_title:
                postfix = ']]'
                if not settings.get('double_brackets', True):
                    postfix = ']'
                location.b += len(postfix)   # we have to replace that, too
                replace_str += postfix + ' ' + selected_text

            self.view.replace(edit, location, replace_str)

            if id_in_title:
                selected_text = new_id + ' ' + selected_text

            # try to find out our own note id
            origin_id, origin_title = get_note_id_and_title_of(self.view)
            create_note(the_file, selected_text, origin_id, origin_title)
            new_view = window.open_file(the_file)
            new_view.set_syntax_file(ZkConstants.Syntax_File)


    def want_event(self):
        # unused
        return True


class ZkShowReferencingNotesCommand(sublime_plugin.TextCommand):
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
        global PANE_FOR_OPENING_RESULTS
        linestart_till_cursor_str, link_region = select_link_in(self.view)
        if not link_region:
            return

        settings = get_settings()
        if F_EXT_SEARCH:
            extension = settings.get('wiki_extension')
            folder = get_path_for(self.view)
            if not folder:
                return
            self.folder = folder
            note_id = cut_after_note_id(self.view.substr(link_region))
            self.friend_note_files = ExternalSearch.search_friend_notes(
                folder, extension, note_id)
            self.friend_note_files = [os.path.basename(f) for f in
                self.friend_note_files]
            if ExternalSearch.EXTERNALIZE:
                nv = self.view.window().open_file(ExternalSearch.external_file(
                    folder))
                self.view.window().set_view_index(nv,
                    PANE_FOR_OPENING_RESULTS, 0)

            else:
                self.view.window().show_quick_panel(self.friend_note_files,
                    self.on_done)
        else:
            new_tab = settings.get('show_search_results_in_new_tab')

            # hack for the find in files panel: select tag in view, copy it
            selection = self.view.sel()
            selection.clear()
            note_id = cut_after_note_id(self.view.substr(link_region))
            selection.add(sublime.Region(link_region.a,
                link_region.a+len(note_id)))
            self.view.window().run_command("copy")
            self.view.window().run_command("show_panel",
                {"panel": "find_in_files",
                "where": get_path_for(self.view),
                "use_buffer": new_tab,})
            # now paste the note-id --> it will land in the "find" field
            self.view.window().run_command("paste")
        return


class ZkReplaceSelectedTextCommand(sublime_plugin.TextCommand):
    def run(self, edit, args):
        text = args['text']
        region = self.view.sel()[0]
        self.view.replace(edit, region, text)


class ZkNewZettelCommand(sublime_plugin.WindowCommand):
    """
    Command that prompts for a note title and then creates a note with that
    title.
    """
    def run(self):
        # try to find out if we come from a zettel
        self.origin = None
        self.o_title = None
        self.insert_link = False
        self.note_body = None
        view = self.window.active_view()
        suggested_title = ''
        if view:
            filn = view.file_name()
            self.origin, self.o_title = get_note_id_and_title_of(view)
            sel = view.sel()
            if len(sel) >=1 and not sel[0].empty():
                suggested_title = view.substr(sel[0])
                if '\n' in suggested_title:
                    lines = suggested_title.split('\n')
                    suggested_title = lines[0]
                    if len(lines) > 1:
                        self.note_body = '\n'.join(lines[1:])
                self.insert_link = True
        self.window.show_input_panel('New Note:', suggested_title, self.on_done, None, None)

    def on_done(self, input_text):
        global PANE_FOR_OPENING_NOTES
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
                # can do here. Non-obtrusively warn the user that this failed
                self.window.status_message(
                'Note cannot be created without a project or an open folder!')
                return

        settings = get_settings()
        extension = settings.get('wiki_extension')
        id_in_title = settings.get('id_in_title')

        new_id = timestamp()
        the_file = os.path.join(folder,  new_id + ' ' + input_text + extension)
        new_title = input_text
        if id_in_title:
            new_title = new_id + ' ' + input_text

        if self.insert_link:
            prefix, postfix = get_link_pre_postfix()
            link_txt = prefix + new_id + postfix
            do_insert_title = settings.get('insert_links_with_titles', False)
            if do_insert_title:
                link_txt += ' ' + input_text
            view = self.window.active_view()
            view.run_command('zk_replace_selected_text', {'args': {'text': link_txt}})
        create_note(the_file, new_title, self.origin, self.o_title, self.note_body)
        new_view = self.window.open_file(the_file)
        post_open_note(new_view, PANE_FOR_OPENING_NOTES)


class ZkGetWikiLinkCommand(sublime_plugin.TextCommand):
    """
    Command that lets you choose one of all your notes and inserts a link to
    the chosen note.
    """
    def on_done(self, selection):
        if selection == -1:
            self.view.run_command(
                'zk_insert_wiki_link', {'args': {'text': '[['}})
            return

        settings = get_settings()
        prefix, postfix = get_link_pre_postfix()
        note_id, title = self.modified_files[selection].split(' ', 1)
        link_txt = prefix + note_id + postfix
        do_insert_title = settings.get('insert_links_with_titles', False)
        if do_insert_title:
            link_txt += ' ' + title

        self.view.run_command(
            'zk_insert_wiki_link', {'args': {'text': link_txt}})

    def run(self, edit):
        folder = get_path_for(self.view)
        if not folder:
            return

        settings = get_settings()
        extension = settings.get('wiki_extension')
        self.files = get_all_notes_for(folder, extension)
        self.modified_files = [os.path.basename(f).replace(extension, '') for f in self.files]
        self.view.window().show_quick_panel(self.modified_files, self.on_done)


class ZkInsertCitationCommand(sublime_plugin.TextCommand):
    def on_done(self, selection):
        if selection < 0:
            return
        settings = get_settings()
        mmd_style = settings.get('citations-mmd-style', None)
        if mmd_style:
            fmt_completion = '[][#{}]'
        else:
            fmt_completion = '[@{}]'

        citekey = self.citekey_list[selection]
        text = fmt_completion.format(citekey)
        self.view.run_command('zk_insert_wiki_link', {'args': {'text': text}})

    def run(self, edit):
        self.citekey_list = []
        self.itemlist = []
        settings = get_settings()
        bibfile = Autobib.look_for_bibfile(self.view, settings)
        if not bibfile:
            return
        entries = Autobib.extract_all_entries(bibfile)
        for citekey, d in entries.items():
            self.citekey_list.append(citekey)
            item = ['{} {} - {} ({})'.format(d['authors'], d['year'],
                d['title'], citekey), d['title']]
            self.itemlist.append(item)
        self.view.window().show_quick_panel(self.itemlist, self.on_done)


class ZkInsertWikiLinkCommand(sublime_plugin.TextCommand):
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
                'zk_insert_wiki_link', {'args': {'text': ZkConstants.TAG_PREFIX}})   # re-used
            return

        tag_txt = self.tags[selection]
        self.view.run_command(
            'zk_insert_wiki_link', {'args': {'text': tag_txt}})  # re-use of cmd

    def run(self, edit):
        folder = get_path_for(self.view)
        if not folder:
            return
        settings = get_settings()
        extension = settings.get('wiki_extension')
        temp = ExternalSearch.EXTERNALIZE
        ExternalSearch.EXTERNALIZE = False
        self.tags = find_all_tags_in(folder, extension)
        ExternalSearch.EXTERNALIZE = temp
        self.view.window().show_quick_panel(self.tags, self.on_done)


class ZkShowAllTagsCommand(sublime_plugin.WindowCommand):
    """
    Command that creates a new view containing a sorted list of all tags
    in all notes
    """
    def run(self):
        global F_EXT_SEARCH
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
        settings = get_settings()
        extension = settings.get('wiki_extension')
        tags = find_all_tags_in(folder, extension)
        tags.sort()
        lines = '# All Tags\n'
        print(lines)
        lines += '\n'.join(['* ' + tag for tag in tags])
        print(lines)

        if ExternalSearch.EXTERNALIZE and not F_EXT_SEARCH:
            with open(ExternalSearch.external_file(folder), mode='w',
                encoding='utf-8') as f:
                f.write(lines)
        ExternalSearch.show_search_results(self.window, folder, 'Tags', lines,
                                                'show_all_tags_in_new_pane')

class ZkShowAllNotesCommand(sublime_plugin.WindowCommand):
    """
    Command that creates a new view containing a sorted list of all notes
    """
    def run(self):
        global F_EXT_SEARCH
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
        settings = get_settings()
        extension = settings.get('wiki_extension')
        note_files = get_all_notes_for(folder, extension)
        note_id_matcher = re.compile('[0-9.]{12,18}')
        note_files = [f for f in note_files if
                        note_id_matcher.match(os.path.basename(f))]
        note_files_str = '\n'.join(note_files)
        ExternalSearch.externalize_note_links(note_files_str, folder, extension,
            prefix='# All Notes:')
        lines = open(ExternalSearch.external_file(folder), mode='r',
            encoding='utf-8', errors='ignore').read().split('\n')
        ExternalSearch.show_search_results(self.window, folder, 'Notes', lines,
                                                'show_all_tags_in_new_pane')


class ZkEnterZkModeCommand(sublime_plugin.WindowCommand):
    """
    Enters the Zettelkasten Mode
    """
    def run(self):
        ZKMode.enter(self.window)


class ZkMultiTagSearchCommand(sublime_plugin.WindowCommand):
    """
    Command for the advanced tag search.
    Prompts for search-spec, executes search, and shows results.
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
        settings = get_settings()
        extension = settings.get('wiki_extension')
        self.folder = folder
        self.extension = extension
        self.window.show_input_panel('#tags and not !#tags:', '', self.on_done,
            None, None)

    def on_done(self, input_text):
        note_ids = TagSearch.advanced_tag_search(input_text, self.folder,
            self.extension)
        if note_ids is None:
            return
        link_prefix, link_postfix = get_link_pre_postfix()
        lines = ['# Notes matching search-spec ' + input_text + '\n']
        results = []
        for note_id in [n for n in note_ids if n]:  # Strip the None
            filn = note_file_by_id(note_id, self.folder, self.extension)
            if filn:
                title = os.path.basename(filn).split(' ', 1)[1]
                title = title.replace(self.extension, '')
                results.append((note_id, title))
        settings = get_settings()
        sort_order = settings.get('sort_notelists_by', 'id').lower()
        if sort_order not in ('id', 'title'):
            sort_order = 'id'
        column = 0
        if sort_order == 'title':
            column = 1
        results.sort(key=itemgetter(column))
        for note_id, title in results:
            line = '* ' + link_prefix + note_id + link_postfix + ' '
            line += title
            lines.append(line)
        if ExternalSearch.EXTERNALIZE:
            with open(ExternalSearch.external_file(self.folder), mode='w',
                encoding='utf-8') as f:
                f.write('\n'.join(lines))
        ExternalSearch.show_search_results(self.window, self.folder,
            'Tag-Search', lines, 'show_all_tags_in_new_pane')


class ZkAutoBibCommand(sublime_plugin.TextCommand):
    """
    Command that just inserts text, usually a link to a note.
    """
    def run(self, edit):
        settings = get_settings()
        mmd_style = settings.get('citations-mmd-style', None)

        bibfile = Autobib.look_for_bibfile(self.view, settings)
        if bibfile:
            text = self.view.substr(sublime.Region(0, self.view.size()))
            ck2bib = Autobib.create_bibliography(text, bibfile, pandoc='pandoc')
            marker = '<!-- references (auto)'
            marker_line = marker
            if mmd_style:
                marker_line += ' -->'
            bib_lines = [marker_line + '\n']
            for citekey in sorted(ck2bib):
                bib = ck2bib[citekey]
                line = '[{}]: {}\n'.format(citekey, bib)
                bib_lines.append(line)
            if not mmd_style:
                bib_lines.append('-->')
            new_lines = []
            for line in text.split('\n'):
                if line.strip().startswith(marker):
                    break
                new_lines.append(line)
            result_text = '\n'.join(new_lines)
            result_text += '\n' + '\n'.join(bib_lines) + '\n'
            complete_region = sublime.Region(0, self.view.size())
            self.view.replace(edit, complete_region, result_text)


class ZkShowImagesCommand(sublime_plugin.TextCommand):
    """
    Show local images inline.
    """
    def run(self, edit):
        settings = get_settings()
        max_width = settings.get('img_maxwidth', None)
        if not max_width:
            max_width = 1024
        if max_width < 0:
            max_width = 1024
        ImageHandler.show_images(self.view, edit, max_width)


class ZkHideImagesCommand(sublime_plugin.TextCommand):
    """
    Hide all shown images.
    """
    def run(self, edit):
        ImageHandler.hide_images(self.view, edit)


class ZkTocCommand(sublime_plugin.TextCommand):
    """
    Auto-insert or refresh a toc in(to) current view.
    """

    def find_toc_region(self):
        """
        Find the entire toc region including start and end markers.
        """
        toc_hdr = ZkConstants.TOC_HDR.replace('(', '\(').replace(')', '\)')
        toc_end = ZkConstants.TOC_END.replace('(', '\(').replace(')', '\)')
        hdr_region = self.view.find(toc_hdr, 0)
        if hdr_region and hdr_region.a > 0:
            end_region = self.view.find(toc_end, hdr_region.b)
            if end_region and end_region.a > hdr_region.b:
                return sublime.Region(hdr_region.a, end_region.b)
        return None

    @staticmethod
    def heading2ref(heading):
        """
        Turn heading into a reference as in `[heading](#reference)`.
        """
        ref = unicodedata.normalize('NFKD', heading).encode('ascii', 'ignore')
        ref = re.sub('[^\w\s-]', '', ref.decode('ascii', errors='ignore')).strip().lower()
        return re.sub('[-\s]+', '-', ref)

    def run(self, edit):
        settings = get_settings()
        suffix_sep = settings.get('toc_suffix_separator', None)
        if not suffix_sep:
            suffix_sep = '_'
        ref_counter = Counter({'': 1})   # '' for unprintable char only headings
        toc_region = self.find_toc_region()
        if not toc_region:
            toc_region = self.view.sel()[0]
        lines = [ZkConstants.TOC_HDR]

        for h_region in self.view.find_by_selector('markup.heading'):
            heading = self.view.substr(h_region)
            ref = self.heading2ref(heading)
            ref_counter[ref] += 1
            if ref_counter[ref] > 1:
                ref = ref + '{}{}'.format(suffix_sep, ref_counter[ref] - 1)

            match = re.match('\s*(#+)(.*)', heading)
            hashes, title = match.groups()
            title = title.strip()
            level = len(hashes) - 1
            line = '    ' * level+ '* [{}](#{})'.format(title, ref)
            lines.append(line)
        lines.append(ZkConstants.TOC_END)
        self.view.replace(edit, toc_region, '')
        self.view.insert(edit, toc_region.a, '\n'.join(lines))


class ZkRenumberHeadingsCommand(sublime_plugin.TextCommand):
    """
    Re-number headings of the current note.
    """

    def run(self, edit):
        current_level = 0
        levels = [0] * 6
        regions_to_skip = 0
        while True:
            h_regions = self.view.find_by_selector('markup.heading')
            h_regions = h_regions[regions_to_skip:]
            if not h_regions:
                break
            regions_to_skip += 1
            h_region = h_regions[0]
            heading = self.view.substr(h_region)
            # print(heading)
            match = re.match('(\s*)(#+)(\s*[1-9.]*\s)(.*)', heading)
            spaces, hashes, old_numbering, title = match.groups()
            level = len(hashes) - 1
            if level < current_level:
                levels[level + 1:] = [0] * (6 - level -1)
            levels[level] += 1
            current_level = level
                # print('resetting levels to', levels)
            numbering = ' ' + '.'.join([str(l) for l in levels[:level+1]]) + ' '
            h_region.a += len(spaces) + len(hashes)   # we're behind the hash
            if old_numbering.strip():   # there is an old numbering to replace
                h_region.b = h_region.a + len(old_numbering)
            else:
                h_region.b = h_region.a
            self.view.replace(edit, h_region, numbering)


class ZkDenumberHeadingsCommand(sublime_plugin.TextCommand):
    """
    Remove numbers of numbered headings of the current note.
    """

    def run(self, edit):
        regions_to_skip = 0
        while True:
            h_regions = self.view.find_by_selector('markup.heading')
            h_regions = h_regions[regions_to_skip:]
            if not h_regions:
                break
            regions_to_skip += 1
            h_region = h_regions[0]
            heading = self.view.substr(h_region)
            match = re.match('(\s*)(#+)(\s*[1-9.]*\s)(.*)', heading)
            spaces, hashes, old_numbering, title = match.groups()
            h_region.a += len(spaces) + len(hashes)   # we're behind the hash
            if old_numbering.strip():   # there is an old numbering to replace
                h_region.b = h_region.a + len(old_numbering)
            self.view.replace(edit, h_region, '')


class ZkSelectPanesCommand(sublime_plugin.WindowCommand):
    """
    Command that prompts for pane numbers for opening notes and results panes.
    """
    def run(self):
        global PANE_FOR_OPENING_NOTES
        for group in range(self.window.num_groups()):
            group_view = self.window.active_view_in_group(group)
            show_str = '<h1 style="color:#FFFFFF;">Pane {}</h1>'.format(group)
            group_view.add_phantom('popup', sublime.Region(0,0), show_str,
                sublime.LAYOUT_BLOCK)
        self.window.show_input_panel('Pane number for opening NOTES:',
            str(PANE_FOR_OPENING_NOTES), self.on_done_first, None,
            self.on_cancel)

    def on_done_first(self, text):
        global F_EXT_SEARCH
        global PANE_FOR_OPENING_NOTES
        global PANE_FOR_OPENING_RESULTS
        try:
            PANE_FOR_OPENING_NOTES = int(text)
        except ValueError:
            self.hide_popups()
            return
        if F_EXT_SEARCH:
            self.window.show_input_panel('Pane number for opening RESULTS:',
                str(PANE_FOR_OPENING_RESULTS), self.on_done_second, None,
               self.on_cancel)
        else:
            self.hide_popups()

    def on_done_second(self, text):
        global PANE_FOR_OPENING_RESULTS
        try:
            PANE_FOR_OPENING_RESULTS = int(text)
        except ValueError:
            pass
        self.hide_popups()

    def on_cancel(self):
        self.hide_popups()

    def hide_popups(self):
        for group in range(self.window.num_groups()):
            group_view = self.window.active_view_in_group(group)
            group_view.erase_phantoms('popup')


class NoteLinkHighlighter(sublime_plugin.EventListener):
    """
    Receives all updates to all views.
    * Highlights [[201710310102]] style links.
    * Enables word completion (ctrl + space) to insert links to notes.
    """
    DEFAULT_MAX_LINKS = 1000

    note_links_for_view = {}
    scopes_for_view = {}
    ignored_views = []
    highlight_semaphore = threading.Semaphore()

    tag_regions = {}
    tag_scopes = {}

    def on_query_context(self, view, key, operator, operand, match_all):
        if key == 'sublime_zk':
            if 'sublime_zk' in view.settings().get('syntax'):
                return True

    def on_query_completions(self, view, prefix, locations):
        """
        Generate auto-completion entries for markdown files, based on
        """
        point = locations[0]
        if view.match_selector(point, 'text.html.markdown') == 0:
            return

        # ignore completion upon <
        word = view.substr(view.word(point)).strip()
        if word.endswith('<'):
            return []

        folder = get_path_for(view)
        if not folder:
            return []

        # we have a path and are in markdown!
        settings = get_settings()
        prefix, postfix = get_link_pre_postfix()
        extension = settings.get('wiki_extension')
        completions = []
        aux = [os.path.basename(f) for f in get_all_notes_for(folder, extension)]
        ids_and_names = [f.split(' ', 1) for f in aux
                                            if f.endswith(extension)
                                            and ' ' in f]
        do_insert_title = settings.get('insert_links_with_titles', False)
        for noteid, notename in ids_and_names:
            completion_str = prefix + noteid + postfix
            if do_insert_title:
                completion_str += ' ' + notename.replace(extension, '')
            completions.append([noteid + ' ' + notename, completion_str])

        # now come the citekeys
        bibfile = Autobib.look_for_bibfile(view, settings)
        if bibfile:
            mmd_style = settings.get('citations-mmd-style', None)
            citekeys = Autobib.extract_all_citekeys(bibfile)
            if mmd_style:
                fmt_key = '#{}'
                fmt_completion = '[][{}]'
            else:
                fmt_key = '@{}'
                fmt_completion = '[{}]'

            for citekey in citekeys:
                citekey = fmt_key.format(citekey)
                completions.append([citekey, fmt_completion.format(citekey)])
        return (completions, 0)

    def on_activated(self, view):
        self.update_note_link_highlights(view)

    # Async listeners for ST3
    def on_load_async(self, view):
        global AUTO_SHOW_IMAGES
        self.update_note_link_highlights_async(view)
        if AUTO_SHOW_IMAGES:
            view.run_command('zk_show_images')

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
        settings = get_settings()
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

        tag_regions = view.find_all(ZkConstants.RE_TAGS_PY())
        NoteLinkHighlighter.tag_regions[view.id()] = tag_regions

        NoteLinkHighlighter.note_links_for_view[view.id()] = note_links

        if should_highlight:
            self.highlight_note_links(view, note_links, tag_regions)

    def update_note_link_highlights_async(self, view):
        NoteLinkHighlighter.highlight_semaphore.acquire()
        try:
            self.update_note_link_highlights(view)
        finally:
            NoteLinkHighlighter.highlight_semaphore.release()

    def highlight_note_links(self, view, note_links, tag_regions):
        """
        Creates a set of regions from the intersection of note_links and scopes,
        underlines all of them.
        """
        settings = get_settings()
        show_bookmarks = settings.get('show_bookmarks_in_gutter')

        # We need separate regions for each lexical scope for ST to use a
        # proper color for the underline
        scope_map = {}
        for note_link in note_links:
            scope_name = view.scope_name(note_link.a)
            scope_map.setdefault(scope_name, []).append(note_link)
        for scope_name in scope_map:
            self.underline_regions(view, scope_name, scope_map[scope_name],
                show_bookmarks, tags = False)
        self.update_view_scopes(view, scope_map.keys(), tags=False)

        scope_map = {}
        for tag_region in tag_regions:
            scope_name = view.scope_name(tag_region.a)
            scope_name = 'markup.zettel.tag'
            scope_map.setdefault(scope_name, []).append(tag_region)
        for scope_name in scope_map:
            self.underline_regions(view, scope_name, scope_map[scope_name],
                show_bookmarks, tags = True)
        self.update_view_scopes(view, scope_map.keys(), tags=True)

    def underline_regions(self, view, scope_name, regions, show_bookmarks, tags):
        """
        Apply underlining to provided regions.
        """
        if show_bookmarks:
            symbol = 'bookmark'
        else:
            symbol = ''

        flags = sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE | sublime.DRAW_SOLID_UNDERLINE
        key = u'clickable-note_links ' + scope_name
        scope = 'markup.zettel.link'

        if tags == True:
            flags = sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE | sublime.DRAW_SOLID_UNDERLINE
            key = 'tag ' + scope_name
            scope = 'markup.zettel.tag'
            symbol = ''

        view.add_regions(key, regions, scope, symbol, flags)


    def update_view_scopes(self, view, new_scopes, tags):
        """
        Store new set of underlined scopes for view.
        Erase underlining from scopes that were once used but are not anymore.
        """
        if not tags:
            old_scopes = NoteLinkHighlighter.scopes_for_view.get(view.id(), None)
            if old_scopes:
                unused_scopes = set(old_scopes) - set(new_scopes)
                for unused_scope_name in unused_scopes:
                    view.erase_regions(u'clickable-note_links ' + unused_scope_name)
            NoteLinkHighlighter.scopes_for_view[view.id()] = new_scopes
        else:
            old_scopes = NoteLinkHighlighter.tag_scopes.get(view.id(), None)
            if old_scopes:
                unused_scopes = set(old_scopes) - set(new_scopes)
                for unused_scope_name in unused_scopes:
                    view.erase_regions(u'tag ' + unused_scope_name)
            NoteLinkHighlighter.tag_scopes[view.id()] = new_scopes

    def on_window_command(self, window, command_name, args):
        global DISTRACTION_FREE_MODE_ACTIVE
        global VIEWS_WITH_IMAGES
        if command_name == 'toggle_distraction_free':
            DISTRACTION_FREE_MODE_ACTIVE[window.id()] = \
                                not DISTRACTION_FREE_MODE_ACTIVE[window.id()]
            for view in [v for v in window.views()
                                                if v.id() in VIEWS_WITH_IMAGES]:
                view.run_command('zk_hide_images')
                view.run_command('zk_show_images')
        elif command_name == 'toggle_full_screen':
            # exit full screen when in distraction free mode exits distraction-
            # free mode!
            if DISTRACTION_FREE_MODE_ACTIVE[window.id()]:
                DISTRACTION_FREE_MODE_ACTIVE[window.id()] = False
                for view in [v for v in window.views()
                                                if v.id() in VIEWS_WITH_IMAGES]:
                    view.run_command('zk_hide_images')
                    view.run_command('zk_show_images')

