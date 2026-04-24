export PARAM=--start-year 1950 --end-year 2025  --i-know-this-is-huge --max-stations 10 --jobs 4 --include-parents
./climate-report.sh --all-under north-america $PARAM
./climate-report.sh --all-under central-america $PARAM
./climate-report.sh --all-under south-america $PARAM
./climate-report.sh --all-under europe $PARAM
./climate-report.sh --all-under asia $PARAM
./climate-report.sh --all-under africa $PARAM
./climate-report.sh --all-under russia $PARAM
./climate-report.sh --all-under antartica  $PARAM
