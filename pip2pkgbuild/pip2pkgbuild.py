#!/usr/bin/python

# json returns unicode strings
# which causes problems for `dict_get` in python2
from __future__ import unicode_literals

import argparse
import fileinput
import json
import logging
import os
import re
import sys
import tarfile
import zipfile

IS_PY2 = sys.version_info.major == 2
if IS_PY2:
    from cStringIO import StringIO as BytesIO
    from urllib2 import urlopen, HTTPError
else:
    from io import BytesIO
    from urllib.request import urlopen
    from urllib.error import HTTPError

META = {
    'name': 'pip2pkgbuild',
    'version': '0.5.0',
    'description': 'Generate PKGBUILD file for a Python module from PyPI',
}

logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] : %(message)s'
)
LOG = logging.getLogger('log')

MODULE_JSON = 'https://pypi.python.org/pypi/{name}/json'
VERSION_MODULE_JSON = 'https://pypi.python.org/pypi/{name}/{version}/json'

MAINTAINER_LINE = '# Maintainer: {name} <{email}>\n'

SPLIT_NAME = """\
pkgbase='{pkgbase}'
pkgname=({pkgname})
"""

SINGLE_NAME = 'pkgname={pkgname}'

HEADERS = """\
_module='{module}'
_src_folder='{src_folder}'
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

PREPARE_FUNC = """\
prepare() {
    cp -a "${srcdir}/${_src_folder}"{,-python2}
}
"""

BUILD_FUNC = """\
build() {{
{statements}
}}
"""

BUILD_STATEMENTS = """\
    cd "${{srcdir}}/${{_src_folder}}{suffix}"
    {python} -m build --wheel --no-isolation"""

BUILD_STATEMENTS_OLD = """\
    cd "${{srcdir}}/${{_src_folder}}{suffix}"
    {python} setup.py build"""

# Note: py_pkgname is double-wrapped in braces since the string will be
# formatted twice
INSTALL_LICENSE = (
    '\n'
    'install -D -m644 {license_path}'
    '"${{{{pkgdir}}}}/usr/share/licenses/{{py_pkgname}}/{license_name}"'
    )

INSTALL_STATEMENT = """\
    {python} -m installer --destdir="${{pkgdir}}" dist/*.whl"""

INSTALL_STATEMENT_OLD = """\
    {python} setup.py install --root="${{pkgdir}}" --optimize=1 --skip-build"""

SUBPKG_DEPENDS = '''
    depends+=({depends})
'''

PACKAGE_FUNC = """\
package{sub_pkgname}() {{{dependencies}
    cd "${{srcdir}}/${{_src_folder}}{suffix}"
{packaging_steps}
}}
"""


def known_licenses():
    """
    :rtype: list[str]
    """
    args = {}
    if IS_PY2:
        args['openhook'] = fileinput.hook_encoded('utf-8')
    else:
        args['encoding'] = 'utf-8'
    return fileinput.input(
            files=('/usr/share/licenses/known_spdx_license_identifiers.txt'),
            **args)


def search_in_iter(i, p):
    """Find the first element in an iterable. matching the predicate

    :type i: list[T]
    :type p: (T) -> bool
    :rtype: T
    """
    for x in i:
        if p(x):
            return x
    return None


def search_in_iter_on(proj, i, p):
    """Find the first element in an iterable whose projection satisfies the
    predicate

    :type proj: (U) -> (T)
    :type i: list[U]
    :type p: (T) -> bool
    :rtype: U
    """
    return search_in_iter(map(proj, i), lambda x: p(proj(x)))


def iter_to_str(i):
    """Convert an iterable to a string contained single quoted elements.

    :type i: list
    :rtype: str
    """
    return ' '.join(map("'{}'".format, i))


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
    return '\n'.join([x for x in lines if x])


def removesuffix(s, suffix):
    """
    :type s: str
    :type suffix: str
    :rtype: str
    """
    if s.endswith(suffix):
        return s[:-len(suffix)]
    return s


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
            self.source = dict_get(src_info, 'url', '')
            self.checksums = dict_get(
                    src_info.get('digests', {}), 'sha256', '')
            self.license_path = None
            self.pep517 = pep517
            if find_license:
                compressed_source = self._download_source(self.source)
                self.license_path = self._find_license_path(compressed_source)
        except KeyError as e:
            raise ParseModuleInfoError(e)

    @staticmethod
    def _download_source(url):
        """Download compressed file at `url` into a compressed object.

        The url should contain the source of the python module.
        :type url: str
        :rtype: Archive|None
        """
        if not url:
            LOG.warning('Given url was empty')
            return None
        # Check to see if the file is a tarfile.
        # Unfortunately, splitext only works for files
        # with single extensions
        filename = os.path.basename(url)

        def _get_archive():
            try:
                return urlopen(url)
            except HTTPError as e:
                LOG.error('Could not retrieve python package for '
                          'license inspection from %s with error %s', url, e)
                return None

        # tar.gz and tar.bz
        if re.match('.*\\.tar\\.(?:gz|bz2)', filename, re.I):
            # The mode needs to be 'r|*', (any type of tarball) which
            # tells tarfile that It should not attempt to
            # seek() or tell() the given
            # object since HTTPResponse doesn't support those operations
            return TarArchive(_get_archive())
        # zip
        elif filename.lower().endswith('.zip'):
            return ZipArchive(_get_archive())
        else:
            LOG.warning("Source url('%s') "
                        'did not have a zip or tar extension', url)
            return None

    @staticmethod
    def _search_compressed_file(compressed_source, match):
        """Shallow depth first sarching in compressed file

        :type compressed_source: Archive
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
            return path.count('/')

        # Prefer matches closer to the root
        sorted_files = sorted(files, key=depth)
        for file_path in sorted_files:
            matched = match(file_path)
            if matched:
                return matched
        return None

    def _find_license_path(self, compressed_source):
        """Determine whether the package source contains a physical license.

        :type compressed_source: Archive
        :rtype: bool|None
        """
        # LICENSE
        # LICENSE.txt
        # license.txt
        # LICENSES.txt
        # license
        find_license = re.compile('.*/LICENSES?(?:\\.(txt|rst|md)|)$')

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

        match = self._search_compressed_file(compressed_source, match_license)
        if match is None:
            LOG.warning('Could not find license file.')
        return match

    # https://wiki.archlinux.org/index.php/PKGBUILD#license
    @staticmethod
    def _get_license(info):
        """
        :type info: dict
        :rtype: str
        """
        def find_known_licenses(p):
            return search_in_iter_on(
                    lambda l: removesuffix(l.lower(), ' license'),
                    known_licenses(), p)

        license_ = find_known_licenses(
            lambda recg: recg == dict_get(info, 'license', ''))

        if license_ is None:
            license_str = search_in_iter(
                dict_get(info, 'classifiers', []),
                lambda clsf: clsf.startswith('License'))

            if license_str is None:
                license_ = 'unknown'
            else:
                license_str = license_str.split('::')[-1].strip()
                license_ = find_known_licenses(
                    lambda recg: recg in license_str)
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
            LOG.warning('Package source not found!')
            LOG.warning('Add it manually and regenerate checksum')
            return {}

        info = search_in_iter(
                urls,
                lambda u: dict_get(u, 'url', '').endswith('.tar.gz'))
        if info is None:
            info = search_in_iter(
                    urls,
                    lambda u: not dict_get(u, 'url', '').endswith('.whl'))
        if info is None:
            info = urls[0]
        return info

    def _get_source(self, url):
        """
        :type url: str
        :rtype: str
        """
        ext = url.split(self.pkgver)[-1]
        return '${_module}-${pkgver}' + ext + '::' + url


class Archive(object):
    """Interface for archive objects (like zip and tar files)"""

    def get_file_listing(self):
        """Return the files present inside of the archive.

        Note tarfile lists the base directory in getnames while
        zipfile does not it its method.

        :rtype: list[str]
        """


class TarArchive(Archive):
    def __init__(self, file):
        self.archive = tarfile.open(fileobj=file, mode='r|*')

    def get_file_listing(self):
        return [tar_info.name for
                tar_info in self.archive.getmembers() if not tar_info.isdir()]


class ZipArchive(Archive):
    def __init__(self, file):
        self.file = zipfile.ZipFile(BytesIO(file.read()))

    def get_file_listing(self):
        # Remove directories from list
        return [name for
                name in self.file.namelist() if not name.endswith('/')]


class Packager(object):

    def __init__(self, module, python=None,
                 depends=None, py2_depends=None, py3_depends=None,
                 mkdepends=None, backend=None,
                 pkgbase=None, pkgname=None, py2_pkgname=None,
                 email=None, name=None):
        """
        :type module: PyModule
        :type python: str
        :type depends: list[str]
        :type py2_depends: list[str]
        :type py3_depends: list[str]
        :type mkdepends: list[str]
        :type backend: str
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

        self.python = 'python2' if IS_PY2 else 'python'
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
        self.mkdepends += self._get_mkdepends(backend)

        if depends is not None:
            self.depends += depends
        if mkdepends is not None:
            self.mkdepends += mkdepends

        self.pkgbase = (
                pkgbase if pkgbase is not None
                else self.pkgname[0] if len(self.pkgname) == 1
                else self.py_pkgname
            )

    def _get_mkdepends(self, backend):
        modules = [backend]
        # Archwiki: [Python_package_guidelines#Standards_based_(PEP_517)]
        if self.pep517:
            modules += ['build', 'installer', 'wheel']
        if self.python == 'multi':
            versions = ['', '2']
        elif self.python == 'python2':
            versions = ['2']
        elif self.python == 'python':
            versions = ['']
        else:
            raise ValueError("Passed invalid python version %s" % self.python)
        return ['python%s-%s' % (v, m) for m in modules for v in versions]

    def _gen_build_func(self, python):
        def gen_statements(py):
            if python == 'multi' and py == 'python2':
                suffix = '-python2'
            else:
                suffix = ''
            build = BUILD_STATEMENTS if self.pep517 else BUILD_STATEMENTS_OLD
            return build.format(
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
            pkgbuild.append(MAINTAINER_LINE.format(
                name=self.name, email=self.email
                ))

        pkg = self.module.source.split('/')[-1]
        src_folder = pkg.split(self.module.pkgver)[0] + self.module.pkgver

        if self.python == 'multi':
            pkgbuild.append(SPLIT_NAME.format(
                pkgbase=self.pkgbase,
                pkgname=iter_to_str(self.pkgname)
                ))
        else:
            pkgbuild.append(SINGLE_NAME.format(
                pkgname=iter_to_str(self.pkgname)
                ))

        pkgbuild.append(HEADERS.format(
            module=self.module.module,
            src_folder=src_folder,
            pkgver=self.module.pkgver,
            pkgdesc=self.module.pkgdesc,
            url=self.module.url,
            depends=iter_to_str(self.depends),
            mkdepends=iter_to_str(self.mkdepends),
            license=self.module.license,
            source=self.module.source,
            checksums=self.module.checksums
        ))

        install = INSTALL_STATEMENT if self.pep517 else INSTALL_STATEMENT_OLD
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
            def package_func(python, py_pkgname, depends, suffix):
                return PACKAGE_FUNC.format(
                    sub_pkgname='_'+self.py_pkgname,
                    dependencies=SUBPKG_DEPENDS.format(
                        depends=iter_to_str(depends)),
                    suffix=suffix,
                    packaging_steps=join_nonempty([
                        license_command.format(py_pkgname=py_pkgname),
                        install.format(python=python)
                    ])
                )

            pkgbuild += [
                PREPARE_FUNC,
                build_fun,
                package_func('python',
                             self.py_pkgname,
                             self.py3_depends,
                             ''),
                package_func('python2',
                             self.py2_pkgname,
                             self.py2_depends,
                             '-python2')
                ]
        else:
            pkgbuild += [
                build_fun,
                PACKAGE_FUNC.format(
                    sub_pkgname='',
                    dependencies='',
                    suffix='',
                    packaging_steps = join_nonempty([
                        license_command.format(py_pkgname=self.pkgname[0]),
                        install.format(python=self.python)
                    ])
                )
            ]

        return '\n'.join(pkgbuild)


def fetch_pymodule(name, version):
    """
    :type name: str
    :type version: str
    :rtype: dict
    """
    def fetch_json(url):
        return json.loads(urlopen(url).read().decode('utf-8'))

    try:
        url = MODULE_JSON.format(name=name)
        info = fetch_json(url)
        if version:
            if info['releases'].get(version) is None:
                raise PythonModuleVersionNotFoundError(
                        '{} {}'.format(name, version))
            url = VERSION_MODULE_JSON.format(name=name, version=version)
            info = fetch_json(url)

    except HTTPError as e:
        if e.code == 404:
            raise PythonModuleNotFoundError('{}'.format(name))
        raise e
    return info


def parse_args(argv):
    argparser = argparse.ArgumentParser(prog=META['name'],
                                        description=META['description'])
    argparser.add_argument(
            'module',
            help='The Python module name')
    argparser.add_argument(
            '-v', '--module-version',
            default='',
            help='Use the specified version of the Python module')
    argparser.add_argument(
            '-p', '--python-version',
            choices=['python', 'python2', 'multi'],
            dest='python',
            help='The Python version on which the PKGBUILD bases')
    argparser.add_argument(
            '-b', '--package-basename',
            type=str,
            dest='pkgbase',
            help='The value for pkgbase. '
            + 'Default: the first value in pkgname')
    argparser.add_argument(
            '-n', '--package-name',
            type=str,
            dest='pkgname',
            help='The value for pkgname. '
            + 'If the package is split, pkgname of the Python 3 package')
    argparser.add_argument(
            '--python2-package-name',
            type=str,
            dest='py2_pkgname',
            help='The pkgname of the Python 2 package')
    argparser.add_argument(
            '-d', '--depends',
            type=str, default=[], nargs='*',
            help='Dependencies for the whole PKGBUILD')
    argparser.add_argument(
            '--python2-depends',
            dest='py2_depends',
            metavar='DEPENDS',
            type=str, default=[], nargs='*',
            help='Dependencies for the Python 2 package in a split package')
    argparser.add_argument(
            '--python3-depends',
            dest='py3_depends',
            metavar='DEPENDS',
            type=str, default=[], nargs='*',
            help='Dependencies for the Python 3 package in a split package')
    argparser.add_argument(
            '-m', '--make-depends',
            dest='mkdepends',
            type=str, default=[], nargs='*',
            help='Packages to add to makedepends (needed for build only)')
    argparser.add_argument(
            '-s', '--build-backend',
            dest='backend',
            type=str, default='setuptools',
            help='Build backend used by package (default guess: setuptools)')
    argparser.add_argument(
            '-o', '--print-out',
            action='store_true',
            help='Print to stdout rather than saving to PKGBUILD file')
    argparser.add_argument(
            '-V', '--version',
            action='version', version='%(prog)s {}'.format(META['version']))
    argparser.add_argument(
            '-l', '--find-license',
            action='store_true', default=False,
            help='Try to find license file in source files')
    argparser.add_argument(
            '--name', dest='name', default=None,
            help='Name for the package maintainer line')
    argparser.add_argument(
            '--email', dest='email', default=None,
            help='Email for the package maintainer line')
    argparser.add_argument(
            '--pep517', dest='pep517', action='store_true',
            default=None,
            help='Prefer PEP517 based installation method if supported')
    argparser.add_argument(
            '--no-pep517', dest='pep517', action='store_false',
            default=None,
            help='Use old-style installation method unconditionally')

    args = argparser.parse_args(argv)

    if bool(args.email) != bool(args.name):
        LOG.error('Must supply either both email and name or neither.')
        sys.exit(1)

    if args.pep517 is None:
        if IS_PY2 or args.python == 'multi' or args.python == 'python2':
            args.pep517 = False
        elif not IS_PY2 or args.python == 'python3':
            args.pep517 = True

    if args.pep517 and (
            (args.python is None and IS_PY2)
            or args.python == 'multi' or args.python == 'python2'
    ):
        LOG.error('PEP517 based installation supports Python 3 packages only.')
        sys.exit(1)

    return args

def main(args):
    args = parse_args(args)

    try:
        module = PyModule(fetch_pymodule(args.module, args.module_version),
                          args.find_license,
                          args.pep517)
    except PythonModuleNotFoundError as e:
        LOG.error('Python module not found: %s', e)
        sys.exit(0)
    except PythonModuleVersionNotFoundError as e:
        LOG.error('Python module version not found: %s', e)
        sys.exit(0)
    except ParseModuleInfoError as e:
        LOG.error('Failed to parse Python module information: %s', e)
        sys.exit(0)

    def filter_options(args, deletes):
        """
        :type args: argparse.Namespace
        :type deletes: list[str]
        :rtype: dict
        """
        opts = dict(vars(args))
        for k in deletes:
            del opts[k]
        return opts

    opts = filter_options(
        args, ['module',
               'module_version',
               'print_out',
               'find_license',
               'pep517'])

    pkgbuild = Packager(module, **opts).generate()

    if args.print_out:
        sys.stdout.write(pkgbuild)
    else:
        with open('PKGBUILD', 'w', encoding="utf-8") as f:
            f.write(pkgbuild)
            LOG.info('Successfully generated PKGBUILD under %s', os.getcwd())


if __name__ == '__main__':
    main(sys.argv[1:])
