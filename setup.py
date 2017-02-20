#!/usr/bin/env python

from __future__ import with_statement

from setuptools import setup, find_packages

with open("README") as readme:
    documentation = readme.read()

setup(
    name="btn",
    version="0.1.0",
    description="Caching API to broadcasthe.net",
    long_description=documentation,
    author="AllSeeingEyeTolledEweSew",
    author_email="allseeingeyetolledewesew@protonmail.com",
    url="http://github.com/AllSeeingEyeTolledEweSew/btn",
    license="Unlicense",
    packages=find_packages(),
    use_2to3=True,
    install_requires=[
        "better-bencode>=0.2.1",
        "PyYAML>=3.12",
        "requests>=2.12.3",
        "token_bucket>=0.1.0",
    ],
    extras_require={
        "feeds": [
            "feedparser>=5.2.1",
        ],
    },
    entry_points={
        "console_scripts": [
            "btn_scrape = btn.cli.scrape:main [feeds]",
        ],
    },
)
