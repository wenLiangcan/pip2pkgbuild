[![PyPi](https://img.shields.io/pypi/v/pip2pkgbuild.svg)](https://pypi.org/project/pip2pkgbuild/)
[![Downloads](https://static.pepy.tech/badge/pip2pkgbuild)](https://pypi.org/project/pip2pkgbuild/)

# About

Re-implementing [`bluepeppers/pip2arch`](https://github.com/bluepeppers/pip2arch>) with some improvements:  

- Supports generating `PKGBUILD` contained [multiple packages](https://www.archlinux.org/pacman/PKGBUILD.5.html#_package_splitting).
- Smarter package license detection.
- License file installation (by @brycepg).
- Maintainer information generation (by @brycepg).
- Supports generating PEP517 based installation instructments.


# Installation

Install from `AUR`:
```shell
$ git clone https://aur.archlinux.org/packages/pip2pkgbuild
$ cd pip2pkgbuild
$ makepkg -si
```

Install from `PyPi`:
```shell
$ pip install pip2pkgbuild
```

Install manually:
```shell
$ cp pip2pkgbuild/pip2pkgbuild.py ~/bin/pip2pkgbuild
$ chmod u+x ~/bin/pip2pkgbuild
```

# Usage
```
 usage: pip2pkgbuild [-h] [-v MODULE_VERSION] [-p {python,python2,multi}]
                     [-b PKGBASE] [-n PKGNAME]
                     [--python2-package-name PY2_PKGNAME]
                     [-d [DEPENDS [DEPENDS ...]]]
                     [--python2-depends [DEPENDS [DEPENDS ...]]]
                     [--python3-depends [DEPENDS [DEPENDS ...]]]
                     [-m [MKDEPENDS [MKDEPENDS ...]]] [-o] [-V] [-l]
                     [--name NAME] [--email EMAIL]
                     module

 Generate PKGBUILD file for a Python module from PyPi

 positional arguments:
   module                The Python module name

 optional arguments:
   -h, --help            show this help message and exit
   -v MODULE_VERSION, --module-version MODULE_VERSION
                         Use the specified version of the Python module
   -p {python,python2,multi}, --python-version {python,python2,multi}
                         The Python version on which the PKGBUILD bases
   -b PKGBASE, --package-basename PKGBASE
                         Specifiy the pkgbase value, the first value in the
                         pkgname array is used by default
   -n PKGNAME, --package-name PKGNAME
                         Specify the pkgname value or the name for the Python 3
                         based package in a package group
   --python2-package-name PY2_PKGNAME
                         Specify the name for the Python 2 based package in a
                         package group
   -d [DEPENDS [DEPENDS ...]], --depends [DEPENDS [DEPENDS ...]]
                         Dependencies for the whole PKGBUILD
   --python2-depends [DEPENDS [DEPENDS ...]]
                         Dependencies for the Python 2 based package in a
                         package group
   --python3-depends [DEPENDS [DEPENDS ...]]
                         Dependencies for the Python 3 based package in a
                         package group
   -m [MKDEPENDS [MKDEPENDS ...]], --make-depends [MKDEPENDS [MKDEPENDS ...]]
                         Dependencies required while running the makepkg
                         command
   -o, --print-out       Print on screen rather than saving to PKGBUILD file
   -V, --version         show program's version number and exit
   -l, --find-license    Attempt to find package license to install
   --name NAME           Your full name for the package maintainer line e.g.
                         'yourFirstName yourLastName'
   --email EMAIL         Your email for the package maintainer line
   --pep517              Prefer PEP517 based installation method if supporting by the module
```


# Examples

Generate a Python 2 based `PKGBUILD` for `Django` with `pkgname` "django":
```shell
$ pip2pkgbuild django -p python2 -n django
```

Generate `PKGBUILD` for `Flask`, containing both Python 2 and 3 packages with `pkgbase` "flask":
```shell
$ pip2pkgbuild flask -p multi -b flask
```
