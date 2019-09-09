#!/usr/bin/fish
set user akbarnes
set repo omf
set img $user/$repo
set ver 0.6

docker build -t $img:ver -t $img:latest .
