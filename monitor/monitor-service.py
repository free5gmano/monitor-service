# -*- coding: UTF-8 -*-
import configparser
import datetime
import json
import logging
import os
# import random
import re
import time

from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kubernetes.client.rest import ApiException
from kubernetes import client, config
from kubernetes.stream import stream
import pymysql


logging.basicConfig(
    level=logging.INFO
)
logger = logging.getLogger(__name__)

ue = {
    'dl_total': 0,
    'ul_total': 0
}
gnb = {}

# load kubeconfig
config.load_kube_config(os.environ.get("KUBE_CONFIG"))

# Load config
config_value = configparser.ConfigParser()
config_value.read(os.environ.get("CONFIG_PATH", "./config.ini"))

# Database
conn = pymysql.connect(
    host=config_value["DATABASE"]["HOST"],
    port=int(config_value["DATABASE"]["PORT"]),
    user=config_value["DATABASE"]["USER"],
    password=config_value["DATABASE"]["PASSWORD"],
    db=config_value["DATABASE"]["DB"],
    charset="utf8"
)
conn.cursor().execute('create database if not exists gnb_info')

# Create Topic & make Producer
KAFKA_SERVER = os.environ.get("KAFKA_SERVER", config_value["KAFKA"]["SERVER"])
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", config_value["KAFKA"]["TOPIC"])

logger.info("="*30)
logger.info(f'KAFKA_SERVER: {KAFKA_SERVER}')
logger.info(f'KAFKA_TOPIC: {KAFKA_TOPIC}')
logger.info("="*30)

topic_flag = True
while topic_flag:
    try:
        kafka_admin = KafkaAdminClient(
            bootstrap_servers=KAFKA_SERVER,
            client_id='test'
        )
        if KAFKA_TOPIC not in kafka_admin.list_topics():
            topic_flag = False
            topic_list = [NewTopic(name=KAFKA_TOPIC, num_partitions=1, replication_factor=1)]
            kafka_admin.create_topics(new_topics=topic_list, validate_only=False)
            logger.info(f'Create Topic: {KAFKA_TOPIC}')
        else:
            topic_flag = False
    except Exception as e:
        logger.error(e)
        time.sleep(1)
        

producer = KafkaProducer(
    bootstrap_servers=KAFKA_SERVER,
    value_serializer=lambda m: json.dumps(m).encode()
)


def recreate_table():
    with conn.cursor() as cursor:
        # select_command = "DELETE FROM ue_info"
        try:
            select_command = "DROP TABLE ue_info"
            cursor.execute(select_command)
            select_command = "DROP TABLE ue_usage"
            cursor.execute(select_command)
        except Exception as e:
            ("error deleting table:" , e)
        select_command = """CREATE TABLE gnb_info.ue_info (
        UE_ID VARCHAR(100) NOT NULL,
        dl_total BIGINT UNSIGNED NOT NULL,
        ul_total BIGINT UNSIGNED NOT NULL,
        gnb_RSRQ FLOAT(8,2) NOT NULL,
        gnb_SINR FLOAT(8,2) NOT NULL,
        gnb_RSRP FLOAT(8,2) NOT NULL,
        CONSTRAINT ue_info_pk PRIMARY KEY (UE_ID))"""
        cursor.execute(select_command)
        select_command = """CREATE TABLE gnb_info.ue_usage (
        UE_ID VARCHAR(100) NOT NULL,
        dl_usage BIGINT UNSIGNED NOT NULL,
        ul_usage BIGINT UNSIGNED NOT NULL,
        Timestamp TIMESTAMP )"""
        cursor.execute(select_command)
        conn.commit()

def pod_exec(name, namespace, command, api_instance):
    exec_command = ["/bin/sh", "-c", command]
    output = ""
    resp = stream(api_instance.connect_get_namespaced_pod_exec,
                  name,
                  namespace,
                  command=exec_command,
                  stderr=True, stdin=False,
                  stdout=True, tty=False,
                  _preload_content=False)

    while resp.is_open():
        resp.update(timeout=1)
        if resp.peek_stdout():
            # print(f"STDOUT: \n{resp.read_stdout()}")
            output = resp.read_stdout()
        if resp.peek_stderr():
            print(f"STDERR: \n{resp.read_stderr()}")

    resp.close()

    if resp.returncode != 0:
        logger.exception("Script failed")
        # raise Exception("Script failed")
    return output


if __name__ == "__main__":
    recreate_table()
    while True:
        try:
            kube_client = client.CoreV1Api()
            api_response = kube_client.list_namespaced_pod(label_selector='app=oai-gnb', namespace='default')
            pod_name = api_response.items[0].metadata.name
            pod_logs = kube_client.read_namespaced_pod_log(name=pod_name, namespace='default', tail_lines=10)
            for pod_log in pod_logs.splitlines(keepends=False):
                if "LCID 4" in pod_log:
                    ue_info = pod_log.split(":")
                    # print(ue_info)
                    ue['RNTI_id'] = ue_info[0].split(" ")[1]
                    tx_rx = re.findall(r"\d{1,20}", ue_info[2].strip())
                    ue['dl_usage'] = int(tx_rx[0]) - ue['dl_total']
                    ue['ul_usage'] = int(tx_rx[1]) - ue['ul_total'] 
                    ue['dl_total'] = int(tx_rx[0])
                    ue['ul_total'] = int(tx_rx[1])
            # ue['RNTI_id'] = random.randint(1, 10) * random.random()
            # ue['dl_usage'] = random.randint(1, 10) * random.random()
            # ue['ul_usage'] = random.randint(1, 10) * random.random()
            # ue['dl_total'] = random.randint(1, 10) * random.random()
            # ue['ul_total'] = random.randint(1, 10) * random.random()
            command = "cat nrRRC_stats.log"
            RRC_logs = pod_exec(pod_name, "default", command, kube_client)
            print("RRC_LOG:", RRC_logs)
            RRC_log = re.findall('((?:[^\n]+\n?){1,6})', RRC_logs)
            for RRC_info in RRC_log[0].splitlines(keepends=False):
                if "resultSSB" in RRC_info:
                    gnb = {}
                    signal_log = re.findall(r"\d+\.\d+", RRC_info.strip())
                    gnb["RSRQ"] = -abs(float(signal_log[0]))
                    gnb["SINR"] = float(signal_log[1])
                    gnb["RSRP"] = int(re.findall(r"\-+\d{1,5}", RRC_info.strip())[0])
            # gnb["RSRQ"] = random.randint(1, 10) * random.random()
            # gnb["SINR"] = random.randint(1, 10) * random.random()
            # gnb["RSRP"] = random.randint(1, 10) * random.random()
            
            data = {
                "ue": ue,
                "gnb": gnb,
                "timestamp": (datetime.datetime.now() - datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
            }
            producer.send(KAFKA_TOPIC, data)
            logger.info(f'Send data: {data}')
            # Save to DB
            with conn.cursor() as cursor:
                select_command = "SELECT * FROM ue_info WHERE UE_ID = %s"
                cursor.execute(select_command, ue['RNTI_id'])
                if cursor.rowcount == 0:
                    insert_command = "INSERT INTO ue_info (UE_ID, dl_total , ul_total, gnb_RSRQ, gnb_SINR, gnb_RSRP ) VALUES (%s, %s, %s, %s, %s, %s)"
                    cursor.execute(insert_command, (ue['RNTI_id'], ue['dl_total'], ue['ul_total'], gnb["RSRQ"], gnb["SINR"], gnb["RSRP"]))
                else:
                    update_command = "UPDATE ue_info SET  dl_total = %s, ul_total=%s, gnb_RSRQ=%s, gnb_SINR=%s, gnb_RSRP=%s  WHERE UE_ID = %s"
                    cursor.execute(update_command, ( ue['dl_total'], ue['ul_total'], gnb["RSRQ"], gnb["SINR"], gnb["RSRP"], ue['RNTI_id']))
                    insert_command = "INSERT INTO ue_usage (UE_ID, dl_usage, ul_usage, Timestamp ) VALUES (%s, %s, %s, %s)"
                    cursor.execute(insert_command, (ue['RNTI_id'], ue['dl_usage'], ue['ul_usage'], data["timestamp"]))
                conn.commit()
            time.sleep(3)
        except IndexError as e:
            print('Found exception in reading the logs')
            time.sleep(3)
        except ApiException as ae:
            print(ae)
            time.sleep(3)
        except Exception as e:
            print(e)
            time.sleep(1)
        