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


class SplitMeta(object):
    """PKGBUILD metadata that can be overridden per split package"""
    # Actually, just the split metadata this script cares about

    def __init__(self, pkgname=None, depends=None, suffix=''):
        self.pkgname = pkgname
        self.depends = depends if depends is not None else []
        self.suffix  = suffix

    def update(self, pkgname=None, depends=None, suffix=None):
        self.pkgname = pkgname if self.pkgname is None else self.pkgname
        self.depends += depends
        self.suffix  = suffix  if self.suffix  is None else self.suffix


def build_meta(python, pkgname, py2_pkgname, py3_depends, py2_depends):
    meta = {}
    if python in {'python', 'multi'}:
        meta['python'] = SplitMeta(
                pkgname = pkgname,
                depends = py3_depends,
                suffix  = ''
                )
    if python in {'python2', 'multi'}:
        meta['python2'] = SplitMeta(
                pkgname = py2_pkgname,
                depends = py2_depends,
                suffix  = '-python2' if python == 'multi' else ''
                )

    if 'python' not in meta and (
            pkgname is not None or py3_depends is not None):
        raise ValueError(('Python 3 package metadata passed: %s\n' +
                         'But requested only Python 2 package to be built!') %
                         str({'pkgname': pkgname,
                              'py3_depends': py3_depends}))
    if 'python2' not in meta and (
            py2_pkgname is not None or py2_depends is not None):
        raise ValueError(('Python 2 package metadata passed: %s\n' +
                         'But requested only Python 3 package to be built!') %
                         str({'py2_pkgname': py2_pkgname,
                              'py2_depends': py2_depends}))

    return meta

class Packager(object):

    def __init__(self, module, meta,
                 mkdepends=None, backend=None, depends=None,
                 pkgbase=None,
                 email=None, name=None):
        """
        :type module: PyModule
        :type python: str
        :type meta: dict[str, SplitMeta]
        :type mkdepends: list[str]
        :type backend: str
        :type depends: list[str]
        :type pkgbase: str
        :type name: str
        :type email: str
        """
        self.module = module
        self.name = name
        self.email = email
        self.pep517 = module.pep517

        self.splits = meta
        self.depends = []
        self.mkdepends = []

        if self.is_split:
            for py in self.splits:
                self.splits[py].update(
                        pkgname = '%s-%s' % (py, module.name),
                        depends = [py])
        else:
            self.depends += self.python_vers

        self.depends += depends
        self.mkdepends += self._get_mkdepends(backend)
        self.mkdepends += mkdepends if mkdepends is not None else []

        self.pkgbase = (
                pkgbase if pkgbase is not None
                else self.pkgname[0] if not self.is_split
                else self.splits['python'].pkgname
            )

    @property
    def is_split(self):
        return len(self.splits) > 1

    @property
    def python_vers(self):
        return self.splits.keys()

    @property
    def pkgname(self):
        return [m.pkgname for m in self.splits.values()]

    def _get_mkdepends(self, backend):
        modules = [backend]
        # Archwiki: [Python_package_guidelines#Standards_based_(PEP_517)]
        if self.pep517:
            modules += ['build', 'installer', 'wheel']
        return ['%s-%s' % (v, m) for m in modules for v in self.python_vers]

    def _steps(self):
        if self.name and self.email:
            yield MAINTAINER_LINE.format(name=self.name, email=self.email)

        pkg = self.module.source.split('/')[-1]
        src_folder = pkg.split(self.module.pkgver)[0] + self.module.pkgver

        if self.is_split:
            yield SPLIT_NAME.format(
                    pkgbase=self.pkgbase,
                    pkgname=iter_to_str(self.pkgname)
                    )
        else:
            yield SINGLE_NAME.format(pkgname=iter_to_str(self.pkgname))

        yield HEADERS.format(
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
                )

        if self.is_split:
            yield PREPARE_FUNC

        build = BUILD_STATEMENTS if self.pep517 else BUILD_STATEMENTS_OLD

        yield BUILD_FUNC.format(statements='\n\n'.join(
            build.format(suffix=meta.suffix, python=py)
            for (py, meta) in self.splits.items())
        )

        install = INSTALL_STATEMENT if self.pep517 else INSTALL_STATEMENT_OLD
        if self.module.license_path:
            license_path = self.module.license_path
            license_command = INSTALL_LICENSE.format(
                license_path=license_path,
                license_name=os.path.basename(license_path)
            )
        else:
            license_command = ''

        for (py, meta) in self.splits.items():
            yield PACKAGE_FUNC.format(
                sub_pkgname=('_'+meta.pkgname) if self.is_split else '',
                dependencies=SUBPKG_DEPENDS.format(
                    depends=iter_to_str(meta.depends)) if meta.depends != []
                    else '',
                suffix=meta.suffix,
                packaging_steps=join_nonempty([
                    license_command.format(py_pkgname=meta.pkgname),
                    install.format(python=py)
                ])
            )

    def generate(self):
        return '\n'.join(self._steps())


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
            default='python2' if IS_PY2 else 'python',
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
            type=str, default=None, nargs='*',
            help='Dependencies for the Python 2 package in a split package')
    argparser.add_argument(
            '--python3-depends',
            dest='py3_depends',
            metavar='DEPENDS',
            type=str, default=None, nargs='*',
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

    meta = build_meta(**{key: vars(args)[key] for key in
                         ['python', 'pkgname', 'py2_pkgname',
                          'py3_depends', 'py2_depends']})

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
               'pep517',
               'python',
               'py2_depends',
               'py3_depends',
               'py2_pkgname',
               'pkgname'])

    pkgbuild = Packager(module, meta, **opts).generate()

    if args.print_out:
        sys.stdout.write(pkgbuild)
    else:
        with open('PKGBUILD', 'w', encoding="utf-8") as f:
            f.write(pkgbuild)
            LOG.info('Successfully generated PKGBUILD under %s', os.getcwd())


if __name__ == '__main__':
    main(sys.argv[1:])
