FROM ubuntu

ENV HTTP_PROXY="http://proxyout.lanl.gov:8080"
ENV HTTPS_PROXY="http://proxyout.lanl.gov:8080"
ENV http_proxy="http://proxyout.lanl.gov:8080"
ENV https_proxy="http://proxyout.lanl.gov:8080"

# Set up 32-bit python
COPY apt.conf /etc/apt
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
RUN cd /opt/omf && python2.7 install.py

# move data so it can be symlinked if needed
RUN mkdir -p /usr/share/omf
RUN mv /opt/omf/omf/data /usr/share/omf/data

CMD /opt/omf/start-web

