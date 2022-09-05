#importing necessary libraries and necessary modules 
from pyspark.sql import SparkSession
from pyspark.sql.functions import *
from pyspark.sql.types import *

#utility fuction to calculate the total income that is coming from every invoice
def calculate_total_cost(items, trn_type):
    if items is not None:
        total_cost = 0 
        item_price = 0
        for item in items:
            item_price = (item['quantity'] * item['unit_price'])
        total_cost = total_cost + item_price
        item_price = 0

        if trn_type == "RETURN":
            return total_cost * -1
        else:
            return total_cost

#utility function to calculate the number of products in every invoice
def calculate_total_item_count(items):
    if items is not None:
        total_count = 0
        for item in items:
            total_count = total_count + item['quantity']
        return total_count

#utility function to determine if invoice is for an order or not
def flag_isOrder(trn_type):
    if trn_type == "ORDER":
        return(1)
    else:
        return(0)

#utility function to determine if invoice is for a return or not
def flag_isReturn(trn_type):
    if trn_type == "RETURN":
        return(1)
    else:
        return(0)

#initialising Spark session    
spark = SparkSession  \
    .builder  \
    .appName("spark-streaming")  \
    .getOrCreate()
spark.sparkContext.setLogLevel('ERROR')

#reading input from Kafka
retail_corp_orderData = spark.readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", "18.211.252.152:9092") \
    .option("startingOffsets","earliest") \
    .option("failOnDataLoss", "false") \
    .option("subscribe", "real-time-project") \
    .load()

#defining schema of a single order
jsonSchema = StructType() \
    .add("invoice_no", LongType()) \
    .add("country", StringType()) \
    .add("timestamp", TimestampType()) \
    .add("type", StringType()) \
    .add("items", ArrayType(StructType([
    StructField("SKU", StringType()),
    StructField("title", StringType()),
    StructField("unit_price", FloatType()),
    StructField("quantity", IntegerType()),    
])))

#reading the data from json in kafka by creating an order stream
orderStream = retail_corp_orderData.select(from_json(col("value").cast("string"), jsonSchema).alias("data")).select("data.*")

#defining the UDFs with the utility functions    
sum_total_order_cost = udf(calculate_total_cost, FloatType())
sum_total_item_count = udf(calculate_total_item_count, IntegerType())  
sum_isOrder = udf(flag_isOrder, IntegerType())
sum_isReturn = udf (flag_isReturn, IntegerType())

#calculating additional columns
updatedOrderStream = orderStream \
    .withColumn("total_cost", sum_total_order_cost(orderStream.items, orderStream.type)) \
    .withColumn("total_items", sum_total_item_count(orderStream.items)) \
    .withColumn("is_order", sum_isOrder(orderStream.type)) \
    .withColumn("is_return", sum_isReturn(orderStream.type)) 

#writing the summarised input values to console
extendedOrderQuery = updatedOrderStream \
    .select("invoice_no", "country", "timestamp", "total_cost", "total_items", "is_order", "is_return") \
    .writeStream \
    .outputMode("append") \
    .format("console") \
    .option("truncate", "false") \
    .trigger(processingTime = "1 minute") \
    .start()

#calculating time-based KPIs
timebasedKPI = updatedOrderStream \
    .withWatermark("timestamp","1 minute") \
    .groupBy(window("timestamp", "1 minute", "1 minute")) \
    .agg(sum("total_cost").alias("total_sale_volume"),
         count("invoice_no").alias("OPM"),
         avg("is_return").alias("rate_of_return"),
         avg("total_cost").alias("average_transaction_size")
        ) \
    .select("window", "OPM", "total_sale_volume", "average_transaction_size", "rate_of_return" )

#writing the time-based KPIs data to HDFS
queryByTime = timebasedKPI.writeStream \
    .format("json") \
    .outputMode("append") \
    .option("truncate","false") \
    .option("path","/user/ec2-user/time_kpi") \
    .option("checkpointLocation","/user/ec2-user/time_kpi_checkpoints") \
    .trigger(processingTime="1 minute") \
    .start()

#calculating time-and-country-based KPIs
timecountrybasedKPI = updatedOrderStream \
    .withWatermark("timestamp", "1 minute") \
    .groupBy(window("timestamp", "1 minute", "1 minute"), "country") \
    .agg(sum("total_cost").alias("total_sale_volume"),
         count("invoice_no").alias("OPM"),
         avg("is_return").alias("rate_of_return")) \
    .select("window", "country", "OPM", "total_sale_volume", "rate_of_return" )

#writing the time-and-country-based KPIs data to HDFS
queryByCountry = timecountrybasedKPI.writeStream \
    .format("json") \
    .outputMode("append") \
    .option("truncate","false") \
    .option("path","/user/ec2-user/country_kpi") \
    .option("checkpointLocation","/user/ec2-user/country_kpi_checkpoints") \
    .trigger(processingTime="1 minute") \
    .start()

#indicating Spark to await termination
extendedOrderQuery.awaitTermination()   
queryByCountry.awaitTermination()
queryByTime.awaitTermination()