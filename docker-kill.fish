#!/usr/bin/fish

docker stop $argv[1]
docker rm $argv[1]
