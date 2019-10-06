#!/usr/bin/fish
set data_dir $HOME/.local/share/omf/data
test ! -d $data_dir; and mkdir -p $data_dir
docker run --name omf -v $data_dir:/opt/omf/omf/data -it -p 5000:5000 akbarnes/omf:latest 
