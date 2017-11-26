"""
                  ___.   .__  .__                            __
        ________ _\_ |__ |  | |__| _____   ____      _______|  | __
       /  ___/  |  \ __ \|  | |  |/     \_/ __ \     \___   /  |/ /
       \___ \|  |  / \_\ \  |_|  |  Y Y  \  ___/      /    /|    <
      /____  >____/|___  /____/__|__|_|  /\___  >    /_____ \__|_ \
           \/          \/              \/     \/           \/    \/
"""
import sublime, sublime_plugin, os, re, subprocess, glob, datetime
from collections import defaultdict
import threading
import io
from subprocess import Popen, PIPE
import struct
import imghdr
import unicodedata
from collections import Counter


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

    # search for tags in files
    RE_TAGS = r"(?<=\s|^)(?<!`)(#+([^#\s.,\/!$%\^&\*;{}\[\]'\"=`~()<>”\\]" \
                                                             r"|:[a-zA-Z0-9])+)"
    # Same RE just for ST python's re module
    ## un-require line-start, sublimetext python's RE doesn't like it
    RE_TAGS_PY = r"(?<=\s)(?<!`)(#+([^#\s.,\/!$%\^&\*;{}\[\]'\"=`~()<>”\\]" \
                                                             r"|:[a-zA-Z0-9])+)"

    # match note links in text
    Link_Matcher = re.compile('(\[+|§)([0-9]{12})(\]+|.?)')
    # Above RE doesn't really care about closing ] andymore
    # This works in our favour so we support [[201711122259 This is a note]]
    # when expanding overview notes

    # TOC markers
    TOC_HDR = '<!-- table of contents (auto) -->'
    TOC_END = '<!-- (end of auto-toc) -->'


# global magic
F_EXT_SEARCH = False

def get_settings():
    return sublime.load_settings(ZkConstants.Settings_File)

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
        note_tag_map = find_all_notes_all_tags_in(folder, extension)
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

    FMT = '<img src="file://{}" width="{}" height="{}">'
    Phantoms = defaultdict(set)

    @staticmethod
    def show_images(view, max_width=1024):
        """
        markup.underline.link.image.markdown
        """
        img_regs = view.find_by_selector('markup.underline.link.image.markdown')
        folder = get_path_for(view)
        if not folder:
            return
        for region in img_regs:
            rel_p = view.substr(region)
            img = os.path.join(folder, rel_p)
            size  = ImageHandler.get_image_size(img)
            if not size:
                continue
            w, h = size
            if w > max_width:
                m = max_width / w
                h *= m
                w = max_width

            view.erase_phantoms(str(region))
            view.add_phantom(str(region), region,
                             ImageHandler.FMT.format(img, w, h),
                             sublime.LAYOUT_BLOCK)
            ImageHandler.Phantoms[view.id()].add(str(region))

    @staticmethod
    def hide_images(view):
        """
        Hide all imgs; use buffered identifiers
        """
        for rel_p in ImageHandler.Phantoms[view.id()]:
            view.erase_phantoms(rel_p)
        del ImageHandler.Phantoms[view.id()]

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
        bibs = ''
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
    def find_citations(text, citekeys):
        """
        Find all mentioned citekeys in text
        """
        founds = re.findall('|'.join(list(citekeys)), text)
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
            pandoc_input = '@' + citekey
            pandoc_out = Autobib.run(pandoc, bibfile, pandoc_input)
            citation, bib = Autobib.parse_pandoc_out(pandoc_out)
            citekey2bib[citekey] = bib
        return citekey2bib

    @staticmethod
    def parse_pandoc_out(pandoc_out):
        """
        Splits pandoc output into citation and bib part
        """
        print('pandoc_out:', repr(pandoc_out))
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
        stdout = stdout.decode('utf-8').replace('\r', '')
        stderr = stderr.decode('utf-8').replace('\r', '')
        # print('pandoc says:', stderr)
        return stdout


class ExternalSearch:
    """
    Static class to group all external search related functions.
    """
    SEARCH_COMMAND = 'ag'
    EXTERNALIZE = '.search_results.md'   # '' to skip

    @staticmethod
    def search_all_tags(folder, extension):
        """
        Create a list of all #tags of all notes in folder.
        """
        output = ExternalSearch.search_in(folder, ZkConstants.RE_TAGS,
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
    def notes_and_tags_in(folder, extension):
        """
        Return a dict {note_id: tags}.
        """
        args = [ExternalSearch.SEARCH_COMMAND, '--nocolor']
        args.extend(['--nonumbers', '-o', '--silent', '--markdown',
            ZkConstants.RE_TAGS, folder])
        ag_out = ExternalSearch.run(args, folder)
        if not ag_out:
            return {}
        note_tags = defaultdict(list)
        note_id = None
        # ag output different to terminal output
        for line in ag_out.split('\n'):
            if not ':' in line:
                continue
            filn, tag = line.rsplit(':', 1)
            note_id = get_note_id_of_file(filn)
            note_tags[note_id].append(tag)
        return note_tags

    @staticmethod
    def search_tagged_notes(folder, extension, tag):
        """
        Return a list of note files containing #tag.
        """
        output = ExternalSearch.search_in(folder, tag, extension)
        prefix = 'Notes tagged with {}:'.format(tag)
        ExternalSearch.externalize_note_links(output, folder, extension, prefix)
        return output.split('\n')

    @staticmethod
    def search_friend_notes(folder, extension, note_id):
        """
        Return a list of notes referencing note_id.
        """
        regexp = '(\[' + note_id + '\])|(§' + note_id + ')'
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
            args.extend(['-l'])
        args.extend(['--silent', '--markdown', regexp, folder])
        return ExternalSearch.run(args, folder)

    @staticmethod
    def run(args, folder):
        """
        Execute ag to run a search, handle errors & timeouts.
        Return output of stdout as string.
        """
        output = b''
        verbose = False
        if verbose:
            print('cmd:', ' '.join(args))
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
        if verbose:
            print(output.decode('utf-8'))
        return output.decode('utf-8')

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

    @staticmethod
    def show_search_results(window, folder, title, lines, new_pane_setting):
        """
        Helper method to display the results either in the external file
        or, if not available, in a new window
        """
        if ExternalSearch.EXTERNALIZE:
            window.open_file(ExternalSearch.external_file(folder))
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
        if not content:
            header = '<!-- Note not found: ' + note_id + ' -->'
        else:
            filename = os.path.basename(note_file).replace(extension, '')
            filename = filename.split(' ', 1)[1]
            header = link_prefix + note_id + link_postfix + ' ' + filename
            header = '<!-- !    ' + header + '    -->'
            footer = '<!-- (End of note ' + note_id + ') -->'
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
        if not link_region:
            return
        note_id = view.substr(link_region)
        cursor_pos = view.sel()[0].begin()
        line_region = view.line(cursor_pos)

        pre, post = get_link_pre_postfix()
        result_lines = TextProduction.embed_note(note_id, folder, extension,
                                                                    pre, post)
        result_lines.append('')   # append a newline for empty line after exp.
        view.insert(edit, line_region.b, '\n' + '\n'.join(result_lines))


def timestamp():
    return '{:%Y%m%d%H%M}'.format(datetime.datetime.now())

def get_link_pre_postfix():
    settings = get_settings()
    extension = settings.get('wiki_extension')
    link_prefix = '[['
    link_postfix = ']]'
    if not settings.get('double_brackets', True):
        link_prefix = '['
        link_postfix = ']'
    return link_prefix, link_postfix

def create_note(filn, title, origin_id=None, origin_title=None):
    params = {
                'title': title,
                'file': os.path.basename(filn),
                'path': os.path.dirname(filn),
                'id': os.path.basename(filn).split()[0],
                'origin_id': origin_id,
                'origin_title': origin_title,
                # don't break legacy
                'origin': origin_id,
              }
    settings = get_settings()
    format_str = settings.get('new_note_template')
    if not format_str:
        format_str = u'# {title}\ntags = \n\n'
    with open(filn, mode='w', encoding='utf-8') as f:
        f.write(format_str.format(**params))

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
    the_file = os.path.join(folder, note_id + '*')
    candidates = [f for f in glob.glob(the_file) if f.endswith(extension)]
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
            for tag in re.findall(ZkConstants.RE_TAGS_PY, line):
                tags.add(tag[0])
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
    inner = search_text.rfind('#')
    if inner >=0:
        # find next consecutive `#`
        for c in reversed(search_text[:inner]):
            if c != '#':
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

def get_note_id_of_file(filn):
    """
    Return the note id of the file named filn or None.
    """
    settings = get_settings()
    extension = settings.get('wiki_extension')
    if filn.endswith(extension):
        # we have a markdown file
        note_id = re.findall('[0-9]{12}', os.path.basename(filn))
        if note_id:
            note_id = note_id[0]
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
        folder = get_path_for(self.view)
        if not folder:
            return

        settings = get_settings()
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


class ZkNewZettelCommand(sublime_plugin.WindowCommand):
    """
    Command that prompts for a note title and then creates a note with that
    title.
    """
    def run(self):
        # try to find out if we come from a zettel
        self.origin = None
        self.o_title = None
        view = self.window.active_view()
        if view:
            filn = view.file_name()
            self.origin, self.o_title = get_note_id_and_title_of(view)
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
                # can do here. Non-obtrusively warn the user that this failed
                self.window.status_message(
                'Note cannot be created without a project or an open folder!')
                return

        settings = get_settings()
        extension = settings.get('wiki_extension')
        id_in_title = settings.get('id_in_title')

        new_id = timestamp()
        the_file = os.path.join(folder,  new_id + ' ' + input_text + extension)

        if id_in_title:
            input_text = new_id + ' ' + input_text

        create_note(the_file, input_text, self.origin, self.o_title)
        new_view = self.window.open_file(the_file)


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

        self.files = [f for f in os.listdir(folder) if f.endswith(extension)]
        self.modified_files = [f.replace(extension, '') for f in self.files]
        self.view.window().show_quick_panel(self.modified_files, self.on_done)


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
                'zk_insert_wiki_link', {'args': {'text': '#'}})   # re-used
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
        lines = '\n'.join(tags)
        ExternalSearch.show_search_results(self.window, folder, 'Tags', lines,
                                                'show_all_tags_in_new_pane')


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
        link_prefix, link_postfix = get_link_pre_postfix()
        lines = ['Notes matching search-spec ' + input_text + '\n']
        for note_id in [n for n in note_ids if n]:  # Strip the None
            filn = note_file_by_id(note_id, self.folder, self.extension)
            if filn:
                title = os.path.basename(filn).split(' ', 1)[1]
                title = title.replace(self.extension, '')
                line = link_prefix + note_id + link_postfix + ' '
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
        bibfile = Autobib.look_for_bibfile(self.view, settings)
        if bibfile:
            text = self.view.substr(sublime.Region(0, self.view.size()))
            ck2bib = Autobib.create_bibliography(text, bibfile, pandoc='pandoc')
            marker = '<!-- references (auto)'
            bib_lines = [marker + '\n']
            for citekey in sorted(ck2bib):
                bib = ck2bib[citekey]
                line = '[@{}]: {}\n'.format(citekey, bib)
                bib_lines.append(line)
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
        ImageHandler.show_images(self.view, max_width)


class ZkHideImagesCommand(sublime_plugin.TextCommand):
    """
    Hide all shown images.
    """
    def run(self, edit):
        ImageHandler.hide_images(self.view)


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
        ref = re.sub('[^\w\s-]', '', ref.decode('ascii')).strip().lower()
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

        for h_region in self.view.find_by_selector('markup.heading.markdown'):
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
            h_regions = self.view.find_by_selector('markup.heading.markdown')
            h_regions = h_regions[regions_to_skip:]
            if not h_regions:
                break
            regions_to_skip += 1
            h_region = h_regions[0]
            heading = self.view.substr(h_region)
            match = re.match('(\s*)(#+)(\s*[1-9.]*\s)(.*)', heading)
            spaces, hashes, old_numbering, title = match.groups()
            level = len(hashes) - 1
            levels[level] += 1
            if level < current_level:
                levels[:current_level] + [0] * (6 - level)
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
            h_regions = self.view.find_by_selector('markup.heading.markdown')
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
        ids_and_names = [f.split(' ', 1) for f in os.listdir(folder)
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
            citekeys = Autobib.extract_all_citekeys(bibfile)
            for citekey in citekeys:
                citekey = '@' + citekey
                completions.append([citekey, '[' + citekey + ']'])
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
