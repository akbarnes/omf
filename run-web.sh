#!/bin/bash
#docker run --name omf -a stdout -a stderr -p 5000:5000 abarnes/omf:latest
docker run --name omf -v Data:/opt/omf/Data -it -p 5000:5000 abarnes/omf:latest 
