import io
import os
import sys

from setuptools import find_packages, setup

here = os.path.abspath(os.path.dirname(__file__))
IS_PY2 = sys.version_info.major == 2
if IS_PY2:
    import imp
    META = imp.load_source(
                '',
                os.path.join(here, 'pip2pkgbuild/pip2pkgbuild.py')
            ).META
else:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
            'pip2pkgbuild',
            'pip2pkgbuild/pip2pkgbuild.py'
           )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    META = module.META

with io.open(os.path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = os.linesep + f.read()

setup(
    name=META['name'],
    version=META['version'],
    description=META['description'],
    long_description=long_description,
    long_description_content_type='text/markdown',

    url='https://github.com/wenLiangcan/pip2pkgbuild',
    author='wenLiangcan',
    author_email='boxeed@gmail.com',
    license='MIT',
    platforms='any',

    classifiers=[
        'Development Status :: 4 - Beta',

        'Environment :: Console',

        'Intended Audience :: End Users/Desktop',
        'License :: OSI Approved :: MIT License',
        'Natural Language :: English',
        'Operating System :: OS Independent',

        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.2',
        'Programming Language :: Python :: 3.3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',

        'Topic :: Software Development :: Build Tools',
        'Topic :: Software Development :: Code Generators',
        'Topic :: System :: Software Distribution',
        'Topic :: Utilities',
    ],

    keywords='Packaging ArchLinux PKGBUILD',
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'pip2pkgbuild = pip2pkgbuild:main',
        ],
    }
)
