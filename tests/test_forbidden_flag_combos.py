import argparse
import pytest
from pip2pkgbuild import main

def test_pep517_py3():
    main(['pip', '-o', '-p', 'python', '--pep517'])
def test_pep517_py2():
    with pytest.raises(SystemExit) as e:
        main(['pip', '-o', '-p', 'python2', '--pep517'])
    assert e.value.code == 1
def test_pep517_multi():
    with pytest.raises(SystemExit) as e:
        main(['pip', '-o', '-p', 'multi', '--pep517'])
    assert e.value.code == 1

def test_nopep517_py3():
    main(['pip', '-o', '-p', 'python', '--no-pep517'])
def test_nopep517_py2():
    main(['pip', '-o', '-p', 'python2', '--no-pep517'])
def test_nopep517_multi():
    main(['pip', '-o', '-p', 'multi', '--no-pep517'])
