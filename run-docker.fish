#!/usr/bin/fish
<<<<<<< HEAD
set data_dir $HOME/.local/share/omf/data
test ! -d $data_dir; and mkdir -p $data_dir
docker run --name omf -v $data_dir:/opt/omf/omf/data -it -p 5000:5000 akbarnes/omf:latest 
=======
set data_dir $HOME/.local/share/omf/omf/Data
set tag 0.7

test ! -d $data_dir; and mkdir -p $data_dir
docker run --name omf -v $data_dir:/opt/omf/omf/Data -it -p 5000:5000 akbarnes/omf:$tag
>>>>>>> 261af01974aa84c5f3010f87220f5d1f7b455f41
