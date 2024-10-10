#!/bin/bash

set -x

docker exec pretalx_ref11 bash -x -e -c "python3 -m pretalx runperiodic"
