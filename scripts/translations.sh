#!/bin/sh
# Update and compile the translations of helpdesk_client in a throwaway container
# (gettext is rarely installed on the host, Windows included).
#
#   scripts/translations.sh            # makemessages (de, en) and compilemessages
#   scripts/translations.sh compile    # compilemessages only
#
# The compiled .mo files are committed: installs from a Git tag build from source.
set -eu

cd "$(dirname "$0")/.."
mode="${1:-all}"

# Git Bash on Windows would rewrite the container paths.
export MSYS_NO_PATHCONV=1

docker run --rm -v "$(pwd):/src" -w /src/helpdesk_client python:3.14-slim sh -eu -c "
  apt-get update -qq >/dev/null
  apt-get install -y -qq --no-install-recommends gettext >/dev/null
  pip install -q --root-user-action=ignore --disable-pip-version-check 'Django>=6.1,<7'
  if [ '$mode' != compile ]; then
    django-admin makemessages -l de -l en --no-obsolete --add-location=file
  fi
  django-admin compilemessages
"
