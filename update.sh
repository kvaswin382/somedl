#!/usr/bin/env bash
# The former updater is deliberately inert, including when invoked as root.
printf '%s\n' 'Automatic engine updating has been removed.' 'Open Settings → Download engines for version checks and per-engine terminal commands.' 'See docs/maintenance.md in your checkout for the manual stop, install and restart steps.'
exit 1
