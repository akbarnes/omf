#!/usr/bin/fish
set data_dir $HOME/.local/share/omf/omf/Data
set tag 0.7

test ! -d $data_dir; and mkdir -p $data_dir
docker run --name omf -v $data_dir:/opt/omf/omf/Data -it -p 5000:5000 akbarnes/omf:$tag
