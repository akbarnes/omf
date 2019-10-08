#!/bin/bash
user=akbarnes
repo=omf
img=$user/$repo
ver=0.8

docker build -t $img:$ver -t $img:latest .
