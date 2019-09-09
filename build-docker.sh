#!/bin/bash
user=akbarnes
repo=omf
img=$user/$repo
ver=0.5

docker build -t $img:$ver -t $img:latest .
