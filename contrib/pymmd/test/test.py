#!python
# -*- coding: utf-8 -*-
"""Unit testing for pymmd"""

import os
import unittest
import textwrap

import pymmd

class TestPyMMD(unittest.TestCase):
    """Test Basic MMD operations."""

    @classmethod
    def setUp(cls):
        """Set up test configurations."""
        here = os.path.abspath(os.path.dirname(__file__))
        cls.test_dir = os.path.join(here, 'test_files')

    def test_valid(self):
        """Test that pymmd loads the MMD library."""
        self.assertTrue(pymmd.valid_mmd())

    def test_version(self):
        """Test MMD version is reported, and a relatively modern version."""
        version = pymmd.version()
        self.assertTrue(version)
        major, minor, patch = [int(ii) for ii in version.split('.', 3)]
        self.assertGreaterEqual(major, 5)
        if major >= 5:
            self.assertGreaterEqual(minor, 4)
            if minor >= 4:
                self.assertGreaterEqual(patch, 0)

    def test_metadata(self):
        """Test basic metadata parsing."""
        base_txt = textwrap.dedent("""\
        title: Test
        author: Me

        # Introduction

        Here is some text.
        """)

        self.assertTrue(pymmd.has_metadata(base_txt, pymmd.COMPLETE))
        self.assertEqual(pymmd.keys(base_txt), ['title', 'author'])
        self.assertEqual(pymmd.value(base_txt, 'title'), 'Test')
        self.assertEqual(pymmd.value(base_txt, 'author'), 'Me')

    def text_empty_metadata(self):
        """Test metadata functions when metadata doesn't exist."""
        base_txt = textwrap.dedent("""\
          # Introduction

          Here is some text.
          """)

        self.assertFalse(pymmd.has_metadata(base_txt, pymmd.COMPLETE))
        self.assertEqual(pymmd.keys(base_txt), [])
        self.assertEqual(pymmd.value(base_txt, 'title'), '')

    def test_convert(self):
        """Test conversion function"""
        with open(os.path.join(self.test_dir, 'test_doc.mmd')) as fp:
            src_doc = fp.read()

        with open(os.path.join(self.test_dir, 'test_doc.html')) as fp:
            html_doc = fp.read()
        with open(os.path.join(self.test_dir, 'test_doc.tex')) as fp:
            tex_doc = fp.read()

        self.assertEqual(pymmd.convert(src_doc), html_doc)
        self.assertEqual(pymmd.convert(src_doc, fmt=pymmd.LATEX), tex_doc)

    def test_convert_from(self):
        """Test convert_from function"""
        with open(os.path.join(self.test_dir, 'transclusion.html')) as fp:
            html_res = fp.read()

        self.assertEqual(pymmd.convert_from(os.path.join(self.test_dir,'transclusion.mmd')), html_res)

if __name__ == '__main__':
    unittest.main()
