FROM python
COPY ./deflock_kml.py /app/

# Add crontab file in the cron directory
ADD cronjob /etc/cron.d/deflock_kml_export

# Give execution rights on the cron job
RUN chmod 0644 /etc/cron.d/deflock_kml_export

# Create the log file to be able to run tail
RUN touch /var/log/cron.log

#Install Cron
RUN apt-get update
RUN apt-get -y install cron

# Run the command on container startup
CMD cron -f