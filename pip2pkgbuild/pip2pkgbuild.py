#!/usr/bin/python

import argparse
import json
import logging
import os
import sys

if sys.version_info.major == 2:
    from urllib2 import urlopen, HTTPError
else:
    from urllib.request import urlopen
    from urllib.error import HTTPError

META = {
    'name': 'pip2pkgbuild',
    'version': '0.2.0',
    'description': 'Generate PKGBUILD file for a Python module from PyPi',
}

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] : %(message)s"
)
LOG = logging.getLogger('log')

HEADERS = """\
pkgbase=('{pkgbase}')
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
md5sums=('{checksums}')
"""

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
    {python} setup.py build"""

PACKAGE_FUNC = """\
package{sub_pkgname}() {{
    depends+=({depends})
    cd "${{srcdir}}/${{_module}}-${{pkgver}}{suffix}"
    {python} setup.py install --root="${{pkgdir}}" --optimize=1 --skip-build
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


class PythonModuleNotFoundError(Exception):
    pass


class ParseModuleInfoError(Exception):
    pass


class PyModule(object):
    def __init__(self, json_data):
        """
        :type json_data: dict
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
            self.source = self._get_source(dict_get(src_info, 'url', ''))
            self.checksums = dict_get(src_info, 'md5_digest', '')
        except KeyError as e:
            raise ParseModuleInfoError(e)

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
            LOG.warning("Package source not found, you need to add it by yourself and regenerate the MD5 checksum")
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
        l = url.replace(self.pkgver, "${pkgver}")
        return l


class Packager(object):

    def __init__(self, module, python=None, depends=None, py2_depends=None,
                 py3_depends=None, mkdepends=None, pkgbase=None, pkgname=None,
                 py2_pkgname=None):
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
        """
        self.module = module

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
            self.mkdepends += ['python-setuptools', 'python2-setuptools']
        elif self.python == 'python2':
            self.pkgname = [self.py2_pkgname]
            self.depends += ['python2']
            self.mkdepends += ['python2-setuptools']
        elif self.python == 'python':
            self.pkgname = [self.py_pkgname]
            self.depends += ['python']
            self.mkdepends += ['python-setuptools']

        if depends:
            self.depends += depends
        if mkdepends:
            self.mkdepends += mkdepends

        self.pkgbase = pkgbase or (
            self.pkgname[0] if len(self.pkgname) == 1 else self.py_pkgname)

    @staticmethod
    def gen_build_func(python):
        def gen_statements(py):
            if python == 'multi' and py == 'python2':
                suffix = '-python2'
            else:
                suffix = ''
            return BUILD_STATEMENTS.format(
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

        build_fun = self.gen_build_func(self.python)

        if self.python == 'multi':
            package_fun = PACKAGE_FUNC.format(
                sub_pkgname='_'+self.py_pkgname,
                depends=iter_to_str(self.py3_depends),
                suffix='',
                python='python'
            )

            py2_package_fun = PACKAGE_FUNC.format(
                sub_pkgname='_'+self.py2_pkgname,
                depends=iter_to_str(self.py2_depends),
                suffix='-python2',
                python='python2'
            )

            pkgbuild += [PREPARE_FUNC, build_fun, package_fun, py2_package_fun]
        else:
            package_fun = PACKAGE_FUNC.format(
                sub_pkgname='',
                depends='',
                suffix='',
                python=self.python
            )
            pkgbuild += [build_fun, package_fun]

        return '\n'.join(pkgbuild)


def fetch_pymodule(name, version=""):
    """
    :type name: str
    :rtype: PyModule
    """
    if version:
        url = 'http://pypi.python.org/pypi/{name}/{version}/json'.format(name=name, version=version)
    else:
        url = 'http://pypi.python.org/pypi/{name}/json'.format(name=name)
    try:
        info = json.loads(urlopen(url).read().decode('utf-8'))
    except HTTPError as e:
        if e.code == 404:
            raise PythonModuleNotFoundError("{} {}".format(name, version).strip())
        else:
            raise e
    return PyModule(info)


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
    args = argparser.parse_args()

    try:
        module = fetch_pymodule(args.module, args.module_version)
    except PythonModuleNotFoundError as e:
        LOG.error("Python module not found: {}".format(e))
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

    opts = get_options(args,
                       ['module', 'module_version', 'print_out'])
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

