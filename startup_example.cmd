# Example IOC shell snippet for generic ecmc StreamDevice command access

# Path to protocol file
epicsEnvSet("STREAM_PROTOCOL_PATH","../ecmc_cfg_stream")

# ecmc asyn port name (must match your ecmcAsynPortDriverConfigure call)
epicsEnvSet("ECMC_PORT","ECMC_ASYN")

# Example ecmc driver init (uncomment/adapt if needed)
# ecmcAsynPortDriverConfigure("$(ECMC_PORT)",2000,0,0,100)

# Load generic stream records
# DRVINFO must be ecmc.asynoctet for text command path
# CMD macro defines fixed query used by ".QRY"
dbLoadRecords("../ecmc_cfg_stream/ecmcCmd.db", \
  "P=IOC:,R=ECMC:,PORT=$(ECMC_PORT),ADDR=0,TIMEOUT=1,DRVINFO=ecmc.asynoctet,CMD=GetControllerError()")

# Usage examples:
# caput IOC:ECMC:CMD "Cfg.SetAxisEnable(1,1)"
# caput IOC:ECMC:QRY.PROC 1
# caget IOC:ECMC:QRY
