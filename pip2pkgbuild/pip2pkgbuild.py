#!/usr/bin/python

# json returns unicode strings
# which causes problems for `dict_get` in python2
from __future__ import unicode_literals

import argparse
import json
import logging
import os
import re
import sys
import tarfile
import zipfile

if sys.version_info.major == 2:
    from cStringIO import StringIO as BytesIO
    from urllib2 import urlopen, HTTPError
else:
    from io import BytesIO
    from urllib.request import urlopen
    from urllib.error import HTTPError

META = {
    'name': 'pip2pkgbuild',
    'version': '0.3.3',
    'description': 'Generate PKGBUILD file for a Python module from PyPI',
}

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] : %(message)s"
)
LOG = logging.getLogger('log')

MODULE_JSON = 'https://pypi.python.org/pypi/{name}/json'
VERSION_MODULE_JSON = 'https://pypi.python.org/pypi/{name}/{version}/json'

MAINTINER_LINE = "# Maintainer: {name} <{email}>\n"

HEADERS = """\
pkgbase='{pkgbase}'
pkgname=({pkgname})
_module='{module}'
pkgver='{pkgver}'
pkgrel=1
pkgdesc="{pkgdesc}"
url="{url}"
depends=({depends})
makedepends=({mkdepends})
license=('{license}')
arch=('any')
source=("{source}")
sha256sums=('{checksums}')
"""

SOURCE_TARGZ = "https://files.pythonhosted.org/packages/source/${_module::1}/$_module/${_module/-/_}-$pkgver.tar.gz"

PREPARE_FUNC = """\
prepare() {
    cp -a "${srcdir}/${_module}-${pkgver}"{,-python2}
}
"""

BUILD_FUNC = """\
build() {{
{statements}
}}
"""

BUILD_STATEMENTS = """\
    cd "${{srcdir}}/${{_module}}-${{pkgver}}{suffix}"
    {python} -m build --wheel --no-isolation"""

BUILD_STATEMENTS_OLD = """\
    cd "${{srcdir}}/${{_module}}-${{pkgver}}{suffix}"
    {python} setup.py build"""

INSTALL_LICENSE = '''\
    install -D -m644 {license_path} "${{{{pkgdir}}}}/usr/share/licenses/{{py_pkgname}}/{license_name}"'''

INSTALL_STATEMENT = '''\
    {python} -m installer --destdir="${{pkgdir}}" dist/*.whl'''

INSTALL_STATEMENT_OLD = '''\
    {python} setup.py install --root="${{pkgdir}}" --optimize=1 --skip-build'''

PACKAGE_FUNC = """\
package{sub_pkgname}() {{
    depends+=({depends})
    cd "${{srcdir}}/${{_module}}-${{pkgver}}{suffix}"
{packaging_steps}
}}
"""


def recognized_licenses():
    """
    :rtype: list[str]
    """
    common = os.listdir('/usr/share/licenses/common')
    return common + ['MIT', 'BSD', 'Python', 'ZLIB']


def search_in_iter(l, p):
    """Find the first element matching the predicate in an iterable.

    :type l: list[T]
    :type p: (T) -> bool
    :rtype: T
    """
    for i in l:
        if p(i):
            return i
    return None


def iter_to_str(i):
    """Convert an iterable to a string contained single quoted elements.

    :type i: list
    :rtype: str
    """
    return ' '.join(map(lambda n: "'{}'".format(n), i))


def dict_get(d, key, default):
    """
    :type d: dict
    :type default T
    :rtype: T
    """
    value = d.get(key)
    return value if isinstance(value, type(default)) else default


def join_nonempty(lines):
    """
    :type lines: list<str>
    :rtype: str
    """
    return '\n'.join([l for l in lines if l])


class PythonModuleNotFoundError(Exception):
    pass


class PythonModuleVersionNotFoundError(Exception):
    pass


class ParseModuleInfoError(Exception):
    pass


class PyModule(object):
    def __init__(self, json_data, find_license=False, pep517=False):
        """
        :type json_data: dict
        :type find_license: bool
        :type pep517: bool
        """
        try:
            info = json_data['info']
            self.module = info['name']
            self.name = self.module.lower()
            self.pkgver = info['version']
            self.pkgdesc = info['summary']
            self.url = info['home_page']
            self.license = self._get_license(info)
            src_info = self._get_src_info(json_data['urls'])
            source_url = dict_get(src_info, 'url', '')
            self.source = self._get_source(source_url)
            self.checksums = dict_get(src_info.get('digests', {}), 'sha256', '')
            self.license_path = None
            self.pep517 = False
            if find_license or pep517:
                compressed_source = self._download_source(source_url)
                if find_license:
                    self.license_path = self._find_license_path(compressed_source)
                self.pep517 = pep517 and self._is_pep517_module(compressed_source)
        except KeyError as e:
            raise ParseModuleInfoError(e)

    @staticmethod
    def _download_source(url):
        """Download compressed file at `url` into a compressed object.

        The url should contain the source of the python module.
        :type url: str
        :rtype: CompressedFacade|None
        """
        if not url:
            LOG.warning("Given url was empty")
            return None
        # Check to see if the file is a tarfile.
        # Unfortunately, splitext only works for files
        # with single extensions
        filename = os.path.basename(url)
        # Accept .tar.gz and .tar.gz files
        tar_match = re.match(".*\.tar\.(?:gz|bz2)", filename, re.I)
        zip_match = filename.lower().endswith('.zip')
        if not tar_match and not zip_match:
            LOG.warning("Source url('%s') "
                        "did not have a zip or tar extension", url)
            return None
        try:
            http_response = urlopen(url)
        except HTTPError as e:
            LOG.error("Could not retrieve python package for "
                      "license inspection from %s with error %s", url, e)
            return None
        if tar_match:
            # The mode needs to be 'r|*', (any type of tarball) which
            # tells tarfile that It should not attempt to
            # seek() or tell() the given
            # object since HTTPResponse doesn't support those operations
            compressed_source = tarfile.open(fileobj=http_response, mode='r|*')
        elif zip_match:
            compressed_source = zipfile.ZipFile(BytesIO(http_response.read()))
        compressed_facade = CompressedFacade(compressed_source)
        return compressed_facade

    @staticmethod
    def _search_compressed_fille(compressed_source, match):
        """Shallow depth first sarching in compressed file

        :type compressed_source: CompressedFacade
        :type match: str -> T|None
        :rtype: T|None
        """
        if compressed_source is None:
            return None
        files = compressed_source.get_file_listing()

        def depth(path):
            """Depth of a file path.

            :type path: str
            :rtype: int
            """
            return path.count("/")

        # Prefer matches closer to the root
        sorted_files = sorted(files, key=depth)
        for file_path in sorted_files:
            matched = match(file_path)
            if matched:
                return matched
        return None

    def _is_pep517_module(self, compressed_source):
        """
        :type compressed_source: CompressedFacade
        :rtype: bool
        """
        def contains_toml(file_path):
            return file_path.endswith('pyproject.toml')
        if self._search_compressed_fille(
          compressed_source, contains_toml) is True:
            return True
        else:
            LOG.warning(self.module + " is not a PEP517 based module.")
            return False

    def _find_license_path(self, compressed_source):
        """Determine whether the package source contains a physical license.

        :type compressed_source: CompressedFacade
        :rtype: bool|None
        """
        # LICENSE
        # LICENSE.txt
        # license.txt
        # LICENSES.txt
        # license
        find_license = re.compile(".*/LICENSES?(?:\.(txt|rst|md)|)$")
        def match_license(file_path):
            """
            :type file_path: str
            :rtype: str|None
            """
            match = find_license.match(file_path, re.I)
            if match:
                # Remove the subfolder file_path from the match
                # Note: path separators inside a zipfile are always '/'
                return ''.join(match.group(0).split('/')[1:])
            return None

        match = self._search_compressed_fille(compressed_source, match_license)
        if match is None:
            LOG.warning("Could not find license file.")
        return match

    # https://wiki.archlinux.org/index.php/PKGBUILD#license
    @staticmethod
    def _get_license(info):
        """
        :type info: dict
        :rtype: str
        """
        def find_recognized(p):
            return search_in_iter(recognized_licenses(), p)

        license_ = find_recognized(
            lambda recg: recg.lower() == dict_get(info, 'license', '').lower())

        if license_ is None:
            license_str = search_in_iter(
                dict_get(info, 'classifiers', []),
                lambda clsf: clsf.startswith('License'))

            if license_str is None:
                license_ = 'unknown'
            else:
                license_str = license_str.split('::')[-1].strip()
                license_ = find_recognized(
                    lambda recg: recg.lower() in license_str.lower())
                if license_ is None:
                    license_ = 'custom:{}'.format(license_str)
        return license_

    @staticmethod
    def _get_src_info(urls):
        """
        :type urls: list[dict]
        :rtype: dict
        """
        if len(urls) == 0:
            LOG.warning("Package source not found, you need to add it by yourself and regenerate checksum")
            return {}

        info = search_in_iter(urls,
                              lambda l: dict_get(l, 'url', '').endswith('.tar.gz'))
        if info is None:
            info = search_in_iter(urls,
                                  lambda l: not dict_get(l, 'url', '').endswith('.whl'))
        if info is None:
            info = urls[0]
        return info

    def _get_source(self, url):
        """
        :type url: str
        :rtype: str
        """
        if url.endswith('.tar.gz'):
            l = SOURCE_TARGZ
        else:
            l = url.replace(self.pkgver, "${pkgver}")
        return l


class CompressedFacade(object):
    """Unify the `tarfile` and `zipfile` interface."""
    ZIPFILE = 1
    TARFILE = 2

    def __init__(self, obj):
        """
        :type obj: tarfile.TarFile | tarfile.ZipFile
        """
        self.obj = obj
        if isinstance(obj, tarfile.TarFile):
            self.compressed_type = CompressedFacade.TARFILE
        elif isinstance(obj, zipfile.ZipFile):
            self.compressed_type = CompressedFacade.ZIPFILE
        else:
            raise ValueError("Given object(%s) not a tar or zipfile", obj)

    def get_file_listing(self):
        """Return the files present inside of the archive.

        Note tarfile lists the base directory in getnames while
        zipfile does not it its method.

        :rtype: list[str]
        """
        if self.compressed_type == CompressedFacade.TARFILE:
            return [tar_info.name for
                    tar_info in self.obj.getmembers() if not tar_info.isdir()]
        else:
            # Remove directories from list
            return [name for
                    name in self.obj.namelist() if not name.endswith("/")]


class Packager(object):

    def __init__(self, module, python=None, depends=None, py2_depends=None,
                 py3_depends=None, mkdepends=None, pkgbase=None, pkgname=None,
                 py2_pkgname=None, email=None, name=None):
        """
        :type module: PyModule
        :type python: str
        :type depends: list[str]
        :type py2_depends: list[str]
        :type py3_depends: list[str]
        :type mkdepends: list[str]
        :type pkgbase: str
        :type pkgname: str
        :type py2_pkgname: str
        :type name: str
        :type email: str
        """
        self.module = module
        self.name = name
        self.email = email
        self.pep517 = module.pep517

        self.python = 'python2' if sys.version_info.major == 2 else 'python'
        if python in ['python', 'python2', 'multi']:
            self.python = python

        python_pkgname = 'python-{}'.format(module.name)
        python2_pkgname = 'python2-{}'.format(module.name)

        self.py_pkgname = pkgname or python_pkgname
        self.py2_pkgname = py2_pkgname or python2_pkgname

        self.depends = []
        self.py2_depends = ['python2']
        self.py3_depends = ['python']
        self.mkdepends = []

        if self.python == 'multi':
            self.pkgname = [self.py_pkgname, self.py2_pkgname]
            if py2_depends:
                self.py2_depends += py2_depends
            if py3_depends:
                self.py3_depends += py3_depends
        elif self.python == 'python2':
            self.pkgname = [self.py2_pkgname]
            self.depends += ['python2']
        elif self.python == 'python':
            self.pkgname = [self.py_pkgname]
            self.depends += ['python']
        self.mkdepends += self._get_mkdepends()

        if depends:
            self.depends += depends
        if mkdepends:
            self.mkdepends += mkdepends

        self.pkgbase = pkgbase or (
            self.pkgname[0] if len(self.pkgname) == 1 else self.py_pkgname)

    def _get_mkdepends(self):
        modules = ['build', 'installer'] if self.pep517 else ['setuptools']
        if self.python == 'multi':
            versions = ['', '2']
        elif self.python == 'python2':
            versions = ['2']
        elif self.python == 'python':
            versions = ['']
        mkdepends = []
        for m in modules:
            for v in versions:
                mkdepends.append('python' + v + '-' + m)
        return mkdepends

    def _gen_build_func(self, python):
        def gen_statements(py):
            if python == 'multi' and py == 'python2':
                suffix = '-python2'
            else:
                suffix = ''
            return (BUILD_STATEMENTS if self.pep517 else BUILD_STATEMENTS_OLD).format(
                suffix=suffix,
                python=py
            )

        if python == 'multi':
            pylist = ['python', 'python2']
        else:
            pylist = [python]

        return BUILD_FUNC.format(
            statements='\n\n'.join(map(gen_statements, pylist))
        )

    def generate(self):
        """
        :rtype: str
        """
        pkgbuild = []

        if self.name and self.email:
            maintainer_line = MAINTINER_LINE.format(
                name=self.name, email=self.email)
            pkgbuild.append(maintainer_line)

        headers = HEADERS.format(
            pkgbase=self.pkgbase,
            pkgname=iter_to_str(self.pkgname),
            module=self.module.module,
            pkgver=self.module.pkgver,
            pkgdesc=self.module.pkgdesc,
            url=self.module.url,
            depends=iter_to_str(self.depends),
            mkdepends=iter_to_str(self.mkdepends),
            license=self.module.license,
            source=self.module.source,
            checksums=self.module.checksums
        )

        pkgbuild.append(headers)

        install_template = INSTALL_STATEMENT if self.pep517 else INSTALL_STATEMENT_OLD
        if self.module.license_path:
            license_path = self.module.license_path
            license_command = INSTALL_LICENSE.format(
                license_path=license_path,
                license_name=os.path.basename(license_path)
            )
        else:
            license_command = ''

        build_fun = self._gen_build_func(self.python)

        if self.python == 'multi':
            packaging_steps = join_nonempty([
                license_command.format(py_pkgname=self.py_pkgname),
                install_template.format(python='python')
            ])
            package_func = PACKAGE_FUNC.format(
                sub_pkgname='_'+self.py_pkgname,
                depends=iter_to_str(self.py3_depends),
                suffix='',
                packaging_steps=packaging_steps
            )

            py2_packaging_steps = join_nonempty([
                license_command.format(py_pkgname=self.py2_pkgname),
                install_template.format(python='python2')
            ])
            py2_package_func = PACKAGE_FUNC.format(
                sub_pkgname='_'+self.py2_pkgname,
                depends=iter_to_str(self.py2_depends),
                suffix='-python2',
                packaging_steps=py2_packaging_steps
            )

            pkgbuild += [PREPARE_FUNC, build_fun, package_func, py2_package_func]
        else:
            packaging_steps = join_nonempty([
                license_command.format(py_pkgname=self.pkgname[0]),
                install_template.format(python=self.python)
            ])
            package_func = PACKAGE_FUNC.format(
                sub_pkgname='',
                depends='',
                suffix='',
                packaging_steps=packaging_steps
            )
            pkgbuild += [build_fun, package_func]

        return '\n'.join(pkgbuild)


def fetch_pymodule(name, version, find_license=False, pep517=False):
    """
    :type name: str
    :type version: str
    :type find_license: bool
    :type pep517: bool
    :rtype: PyModule
    """
    def fetch_json(url):
        return json.loads(urlopen(url).read().decode('utf-8'))

    try:
        url = MODULE_JSON.format(name=name)
        info = fetch_json(url)
        if version:
            if info['releases'].get(version) is None:
                raise PythonModuleVersionNotFoundError("{} {}".format(name, version))
            else:
                url = VERSION_MODULE_JSON.format(name=name, version=version)
                info = fetch_json(url)

    except HTTPError as e:
        if e.code == 404:
            raise PythonModuleNotFoundError("{}".format(name))
        else:
            raise e
    return PyModule(info, find_license, pep517)


def main():
    argparser = argparse.ArgumentParser(prog=META['name'],
                                        description=META['description'])
    argparser.add_argument('module',
                           help='The Python module name')
    argparser.add_argument('-v', '--module-version',
                           default='',
                           help="Use the specified version of the Python module")
    argparser.add_argument('-p', '--python-version',
                           choices=['python', 'python2', 'multi'],
                           dest='python',
                           help='The Python version on which the PKGBUILD bases')
    argparser.add_argument('-b', '--package-basename',
                           type=str,
                           dest='pkgbase',
                           help='Specifiy the pkgbase value, the first value in the pkgname array is used by default')
    argparser.add_argument('-n', '--package-name',
                           type=str,
                           dest='pkgname',
                           help='Specify the pkgname value or the name for the Python 3 based package in a package group')
    argparser.add_argument('--python2-package-name',
                           type=str,
                           dest='py2_pkgname',
                           help='Specify the name for the Python 2 based package in a package group')
    argparser.add_argument('-d', '--depends',
                           type=str, default=[], nargs='*',
                           help='Dependencies for the whole PKGBUILD')
    argparser.add_argument('--python2-depends',
                           dest='py2_depends',
                           metavar='DEPENDS',
                           type=str, default=[], nargs='*',
                           help='Dependencies for the Python 2 based package in a package group')
    argparser.add_argument('--python3-depends',
                           dest='py3_depends',
                           metavar='DEPENDS',
                           type=str, default=[], nargs='*',
                           help='Dependencies for the Python 3 based package in a package group')
    argparser.add_argument('-m', '--make-depends',
                           dest='mkdepends',
                           type=str, default=[], nargs='*',
                           help='Dependencies required while running the makepkg command')
    argparser.add_argument('-o', '--print-out',
                           action="store_true",
                           help='Print on screen rather than saving to PKGBUILD file')
    argparser.add_argument('-V', '--version',
                           action='version', version='%(prog)s {}'.format(META['version']))
    argparser.add_argument('-l', '--find-license',
                           action='store_true', default=False,
                           help='Attempt to find package license to install')
    argparser.add_argument('--name', dest='name', default=None,
                           help="Your full name for the package maintainer "
                                "line e.g. 'yourFirstName yourLastName'")
    argparser.add_argument('--email', dest='email', default=None,
                           help="Your email for the package maintainer line")
    argparser.add_argument('--pep517', dest='pep517', action='store_true', default=False,
                           help='Prefer PEP517 based installation method if supporting by the module')

    args = argparser.parse_args()

    if bool(args.email) != bool(args.name):
        LOG.error("Must supply both email and name or neither.")
        sys.exit(1)

    try:
        module = fetch_pymodule(args.module, args.module_version,
                                args.find_license,
                                args.pep517)
    except PythonModuleNotFoundError as e:
        LOG.error("Python module not found: {}".format(e))
        sys.exit(0)
    except PythonModuleVersionNotFoundError as e:
        LOG.error("Python module version not found: {}".format(e))
        sys.exit(0)
    except ParseModuleInfoError as e:
        LOG.error("Failed to parse Python module information: {}".format(e))
        sys.exit(0)

    def get_options(args, deletes):
        """
        :type args: argparse.Namespace
        :type deletes: list[str]
        :rtype: dict
        """
        opts = dict(vars(args))
        for k in deletes:
            del opts[k]
        return opts

    opts = get_options(
        args, ['module', 'module_version', 'print_out', 'find_license', 'pep517'])
    packager = Packager(module, **opts)
    pkgbuild = packager.generate()

    if args.print_out:
        sys.stdout.write(pkgbuild)
    else:
        with open('PKGBUILD', 'w') as f:
            f.write(pkgbuild)
            LOG.info("Successfully generated PKGBUILD under {}".format(os.getcwd()))


if __name__ == '__main__':
    main()

