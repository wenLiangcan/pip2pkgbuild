pkgbase=('python-requests')
pkgname=('python-requests')
_module='requests'
pkgver='2.7.0'
pkgrel=1
pkgdesc="Python HTTP for Humans."
url="http://python-requests.org"
depends=('python')
makedepends=('python-setuptools')
license=('Apache')
arch=('any')
source=("https://pypi.python.org/packages/source/r/requests/requests-${pkgver}.tar.gz")
md5sums=('29b173fd5fa572ec0764d1fd7b527260')

package() {
    depends+=()
    cd "${srcdir}/${_module}-${pkgver}"
    python setup.py install --root="${pkgdir}" --optimize=1
}
