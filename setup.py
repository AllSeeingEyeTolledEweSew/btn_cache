# The author disclaims copyright to this source code. Please see the
# accompanying UNLICENSE file.

from typing import List
from typing import Tuple
import distutils.cmd
import importlib.resources
import pathlib
import subprocess

from setuptools import find_packages
from setuptools import setup


class FormatCommand(distutils.cmd.Command):

    description = "Run autoflake and yapf on python source files"
    user_options:List[Tuple] = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run_isort(self):
        subprocess.check_call(["isort", "-rc", "-y"])

    def run_yapf(self):
        subprocess.check_call(["yapf", "-i", "-r", "."])
        # yapf does not fix certain hanging indents currently, see
        # https://github.com/google/yapf/issues/769
        # We work around this by calling selective autopep8 after yapf.
        subprocess.check_call([
            "autopep8", "-i", "-r", "--select", "E125", "."])

    def run_autoflake(self):
        subprocess.check_call([
            "autoflake", "-i", "-r", "--remove-all-unused-imports",
            "--remove-duplicate-keys", "--remove-unused-variables", "."])

    def run(self):
        self.run_isort()
        self.run_yapf()
        self.run_autoflake()

class GenerateSqlCommand(distutils.cmd.Command):

    description = "Generate SQL scripts from templates"

    user_options = [("debug", None, "generate scripts with debug enabled")]

    boolean_options = ["debug"]

    def initialize_options(self) -> None:
        self.debug:bool = False

    def finalize_options(self) -> None:
        pass

    def run(self) -> None:
        import jinja2
        env = jinja2.Environment(loader=jinja2.PackageLoader("btn"))
        tpl = env.get_template("metadata_1.0.0.sql")
        contents = tpl.render(DEBUG=self.debug)
        path = pathlib.Path().joinpath("btn", "sql", "metadata_1.0.0.sql")
        with path.open(mode="w") as fp:
            fp.write(contents)



with open("README") as readme:
    documentation = readme.read()

setup(
    name="btn",
    version="2.0.0",
    description="Local metadata cache for broadcasthe.net",
    long_description=documentation,
    author="AllSeeingEyeTolledEweSew",
    author_email="allseeingeyetolledewesew@protonmail.com",
    url="http://github.com/AllSeeingEyeTolledEweSew/btn",
    license="Unlicense",
    packages=find_packages(),
    package_data={"btn": ["sql/*.sql"]},
    install_requires=[
        "better-bencode>=0.2.1",
        "requests>=2.24.0,<3",
        "feedparser>=5.2.1", ],
    entry_points={
        "console_scripts": [
            "btn_scrape = btn.cli.btn_scrape:main", ], },
    cmdclass={
        "format": FormatCommand,
        "generate_sql": GenerateSqlCommand},
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "License :: Public Domain",
        "Programming Language :: Python",
        "Topic :: Communications :: File Sharing",
        "Topic :: Database",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Networking",
        "Operating System :: OS Independent",
        "License :: Public Domain", ],
)
