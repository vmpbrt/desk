#!/bin/sh
# git asks for Username then Password; token comes only from the environment.
case "$1" in
  Username*) echo "x-access-token" ;;
  *) printf '%s\n' "$GH_DESK_TOKEN" ;;
esac
