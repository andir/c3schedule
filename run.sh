#!/usr/bin/env bash
git submodule init
git submodule update
test -d env || pyvenv env
source env/bin/activate
test -e requirements.txt && pip install --upgrade -r requirements.txt

if [ -e sopel.conf ]; then
	sopel -c sopel.conf
else
	echo -e "\e[0;31mYou should copy sopel.conf.example to sopel.conf and modify it according to your needs\e[0m" >&2
	exit 1
fi
