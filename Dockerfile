FROM abarnes/python2.7-32

ADD . /opt/omf
RUN cd /opt/omf && python2.7 install.py
CMD cd /opt/omf && python2.7 web.py


