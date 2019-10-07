#!/bin/bash

test ! -d 
docker run --name omf -v $HOME/.local/share/omf/omf/Data:/opt/omf/omf/Data -it -p 5000:5000 abarnes/omf:latest 
