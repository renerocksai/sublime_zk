"""Setuptools file for a MultiMarkdown Python wrapper."""
from codecs import open
from os import path
from distutils.core import setup
from distutils.util import get_platform
from setuptools import find_packages, Command, Distribution
from pymmd import build_mmd
import sys
import glob

here = path.abspath(path.dirname(__file__))

class BuildMMDCommand(Command):
    """Build MMD to include in package."""
    description = "Downloads and builds MultiMarkdown shared library and adds it to the distribution"
    user_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        build_mmd(path.join(here, 'pymmd', 'files'))

class BinaryDistribution(Distribution):
    """Forcing distribution to not be considered pure"""
    def is_pure(self):
        return False

if "download_mmd" in sys.argv and "bdist_wheel" in sys.argv:
    sys.argv.append('--plat-name')
    sys.argv.append(get_platform())

with open(path.join(here, 'README.rst'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='pymmd',
    version='0.4.0',
    description='Python wrapper for the MultiMarkdown library.',
    long_description=long_description,
    license='MIT',
    author='Jason Ziglar',
    author_email='jasedit@gmail.com',
    url="https://github.com/jasedit/pymmd",
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Text Processing :: Markup',
        'Topic :: Text Processing :: Filters',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 3'
    ],
    packages=find_packages(),
    package_data={'pymmd': ['files/*']},
    cmdclass={'download_mmd': BuildMMDCommand},
    distclass=BinaryDistribution
    )
