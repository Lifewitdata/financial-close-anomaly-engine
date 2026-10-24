# Databricks notebook (prod ingestion): ERP CSV -> Delta Bronze
# In local dev this step is replaced by dbt seeds - same schema, same grain.
# %pip install delta-spark
from pyspark.sql import functions as F
# spark.read.option("header", True).csv("/Volumes/finance_catalog/raw/erp/raw_transactions.csv") \
#   .withColumn("_ingested_at", F.current_timestamp()) \
#   .write.format("delta").mode("overwrite").saveAsTable("finance_catalog.bronze.raw_transactions")
print("Prod stub: see README - local build uses dbt seeds with identical columns.")
