#!/bin/bash
#docker run --name omf -a stdout -a stderr -p 5000:5000 abarnes/omf:latest
docker run --name omf -it -p 5000:5000 abarnes/omf:latest /bin/bash
