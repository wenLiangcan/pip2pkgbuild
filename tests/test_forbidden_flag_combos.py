"""
Test forbidden/supported flag combinations work correctly
"""

import pytest
import pip2pkgbuild


def main(*args):
    """
    Wrapper around pip2pkgbuild.main.
    It is especially used to set argv[0], but we also set the program to print
    the PKGBUILD to stdout to prevent side-effects
    """
    pip2pkgbuild.main(['pip2pkgbuild', '-o', *args])


@pytest.mark.parametrize('python, pep517', [
    ('python',  '--pep517'),
    ('python',  '--no-pep517'),
    ('python2', '--no-pep517'),
    ('multi',   '--no-pep517')
    ])
def test_supported_pep517(python, pep517):
    """Test supported python/pep517 combinations"""
    main('pip', '-p', python, pep517)


@pytest.mark.parametrize('python, pep517', [
    ('python2', '--pep517'),
    ('multi',   '--pep517')
    ])
def test_forbidden_pep517(python, pep517):
    """Test forbidden python/pep517 combinations"""
    with pytest.raises(SystemExit) as e:
        main('pip', '-p', python, pep517)
    assert e.value.code == 1


@pytest.mark.parametrize('python, pydeps', [
    ('python',  ['--python3-depends', 'pylint']),
    ('python2', ['--python2-depends', 'pylint']),
    ('multi',   ['--python2-depends', 'pylint']),
    ('multi',   ['--python3-depends', 'pylint']),
    ('multi',   ['--python2-depends', 'pylint', '--python3-depends', 'pylint']),
    ('multi',   ['--python3-depends', 'pylint', '--python2-depends', 'pylint'])
    ])
def test_supported_pydep(python, pydeps):
    """Test supported python/pydep combinations"""
    main('pip', '-p', python, *pydeps)


@pytest.mark.parametrize('python, pydeps', [
    ('python',  ['--python2-depends', 'pylint']),
    ('python',  ['--python2-depends', 'pylint', '--python3-depends', 'pylint']),
    ('python2', ['--python3-depends', 'pylint']),
    ('python2', ['--python2-depends', 'pylint', '--python3-depends', 'pylint'])
    ])
@pytest.mark.xfail
def test_forbidden_pydep(python, pydeps):
    """Test forbidden python/pydep combinations"""
    main('pip', '-p', python, *pydeps)
