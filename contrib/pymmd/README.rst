.. image:: https://img.shields.io/pypi/v/pymmd.svg
    :target: https://pypi.python.org/pypi/pymmd/
    :alt: Latest Version
.. image:: https://img.shields.io/pypi/dm/pymmd.svg
    :target: https://pypi.python.org/pypi/pymmd/
    :alt: Downloads
.. image:: https://img.shields.io/pypi/l/pymmd.svg
    :target: https://pypi.python.org/pypi/pymmd/
    :alt: License
.. image:: https://landscape.io/github/jasedit/pymmd/master/landscape.svg?style=flat
    :target: https://landscape.io/github/jasedit/pymmd/master/
    :alt: Code Health

pymmd
============

Python wrapper for `MultiMarkdown <https://github.com/fletcher/MultiMarkdown-5>`_, which converts MultiMarkdown flavored text into one of several outputs formats. This package directly wraps the reference implementation, and provides a simple interface to the library.

The `ctypes <https://docs.python.org/2/library/ctypes.html>`_ package is used to wrap libMultiMarkdown in a portable fashion.

Installation
=============

This package requires MultiMarkdown installed as a shared library in order to function. For Windows and macOS, the shared library is included in the distributed package.

This package can be installed via pypi:

.. code:: bash

  pip install pymmd

For Linux users, the shared library can be installed by executing:

.. code:: bash

  python -c "import pymmd; pymmd.build_mmd()"

Which will download, build, and install the required library within the package's directory. This may need to be run with `sudo` if the package is installed to a system-level site-packages directory.

Verifying the package is working as intended can be accomplished via a simple test command, which should print out the MultiMarkdown version in use:

.. code:: bash

  python -c "import pymmd; print(pymmd.version())"

Examples
=============

Converting a string of MultiMarkdown directly to various outputs:

.. code:: python

  import pymmd
  # Generate string of MultiMarkdown text named data

  html_output = pymmd.convert(data)
  latex_output = pymmd.convert(data, fmt=pymmd.LATEX)

  #Generate a snippet
  html_snippet = pymmd.convert(data, ext=pymmd.SNIPPET)

Conversion can be performed with the `Transclusion <http://fletcher.github.io/MultiMarkdown-5/transclusion>`_ capabilities of MultiMarkdown, either by specifying the directory name:

.. code:: python
  import pymmd

  with open('./document.mmd') as fp:
    src = fp.read()
  output = pymmd.convert(src, dname='.')

Files can also be converted directly from file:

.. code:: python

  import pymmd

  #MMD file named data.mmd

  html_output = pymmd.convert_from("./data.mmd")
