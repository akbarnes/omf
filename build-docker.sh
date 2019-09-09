#!/bin/bash
user=akbarnes
repo=omf
img=$user/$repo

docker build -t $img:0.4 -t $img:latest .
