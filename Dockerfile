FROM abarnes/python2.7-32

ADD . /omf
RUN apt-get install sudo
RUN ln -s /usr/bin/python2.7 /usr/bin/python
RUN python2.7 /omf/install.py
CMD python2.7 /omf/web.py


