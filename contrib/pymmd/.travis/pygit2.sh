#!/bin/sh
#Script to install pygit2 from source, from https://gist.github.com/olivier-m/5755638
set -e

if [ "${VIRTUAL_ENV}" = "" ]; then
    echo "Error: Not in a virtual env"
    exit 1
fi

OS=$(uname -s)

set -u

SRC="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )/_tmp"
TARGET=${VIRTUAL_ENV}
VERSION='v0.25.1'

test -d ${SRC} || mkdir ${SRC}

cd ${SRC}

test -d libgit2 || git clone git://github.com/libgit2/libgit2.git
test -d pygit2 || git clone git://github.com/libgit2/pygit2.git

# Building libgit2
cd ${SRC}/libgit2
git checkout ${VERSION}
rm -rf build && mkdir build
cd build
cmake .. -DCMAKE_INSTALL_PREFIX=${TARGET}
cmake --build . --target install

# Building pygit2
cd ${SRC}/pygit2
git checkout ${VERSION}
LIBGIT2=${TARGET} python setup.py build_ext -R ${TARGET}/lib
python setup.py build

if [ "${OS}" = "Darwin" ]; then
    install_name_tool -add_rpath ${TARGET}/lib $(find build -name '_pygit2.so')
    install_name_tool -change libgit2.0.dylib @rpath/libgit2.0.dylib $(find build -name '_pygit2.so')
fi

python setup.py install

rm -rf ${SRC}
