FROM ubuntu

# Set up 32-bit python
RUN dpkg --add-architecture i386
RUN apt-get update
RUN apt-get install -y git python2.7:i386 sudo
RUN ln -s /usr/bin/python2.7 /usr/bin/python

# Set up tzdata
ADD setup-tzdata /usr/local/bin/setup-tzdata
RUN chmod 755 /usr/local/bin/setup-tzdata
RUN /usr/local/bin/setup-tzdata

# Set up OMF
ADD . /opt/omf
RUN mkdir -p /usr/share/omf
RUN mv /opt/omf/Data /usr/share/omf/data
RUN cd /opt/omf && python2.7 install.py

# move data so it can be symlinked if needed
RUN mkdir -p /usr/share/omf
RUN mv /opt/omf/omf/data /usr/share/omf/data

CMD cd /opt/omf/omf && python2.7 web.py

