# [XPGFS] NOAA Global Weather (Xplane 12)
I created a new version of NOAAWeather originally created by [Joan](https://github.com/joanpc) and ported to python3 by [pbuckner](https://github.com/pbuckner).

You can find original version [here](http://x-plane.joanpc.com/plugins/xpgfs-noaa-weather) (I guess it will not work in XP12).

And here the version of [pbuckner](https://github.com/pbuckner) ported to Python 3.x:
https://github.com/pbuckner/XplaneNoaaWeather

> [!IMPORTANT]
> You cannot have both python 3 and python 2 interfaces and plugins!

## Features
At this stage the plugin in its X-Plane 12 version is almost only monitoring what XP Real Weather engine is doing.

As XP12.4 still has not the capability to depict correctly snow cover, GFS information are downloaded from NOAA server and the plugin will add snow cover according to them.

XP12 in cold weather conditions is not simulating tarmac treatment, so if it detects heavy icing conditions they are reflected into runway friction, resulting in completely unrealistic situation.\
This plugin has a feature that simulates tarmac treatment, lowering the slipperiness to maintain grip into domains that would permit the airport to be active. 

Concerning all other data, as Real Weather already takes data from GFS Grib files, probably there will be no need to download them anylonger.

X-Plane 12 is continuously updated, so I will consider if some of the XP11 version features will still be needed.

- Adds snow cover using GFS data information
- Adjust runway friction level to simulate tarmac treatment
- monitors XP12 real weather behavior
- METAR query window that displays both from XP12 Real Weather and chosen source (NOAA, IVAO or VATSIM servers)

> [!NOTE]
> latest versions of AviTab do not need METAR.rwx file anymore, so function to write this file is deprecated

> [!WARNING]
> Be aware that using XP12 Real Weather as data source when flying online could give you outdated information.
This means that ATC information, ATIS, runway in use and generally weather for any pilot not using XP12 could be different from what you expect looking at your data.
Lately (XP12.05r1 and above) Real Weather METAR is a lot improved compared to previous versions and almost always up-to-date. 
Anyway, be respectful of other users, you are the one using wrong data.

## Requirements
- MacOS 10.14, Windows 7 and Linux kernel 4.0 and above
(tested using macOS 12.7.6)
- X-Plane **12.4 and above** (not tested with previous versions) 
- pbuckner's [XPPython3 plugin **4.6.0 or above**](https://xppython3.readthedocs.io/en/latest/index.html) (tested using version 4.6.1)

> [!NOTE]
> **(*) Latest XPPython3 [plugin version (4.3.0 and above)](https://xppython3.readthedocs.io/en/latest/index.html) will contain all python needed libraries, so it won't be necessary to install Python on the machine anymore. Read carefully XPPython3 plugin documentation**

> [!IMPORTANT]
> **Starting from version 12.3, NOAAWeather requires XPPython3 version 4.6.0 or above!\
If you wish to keep using previous versions (you really shouldn't), use NOAAWeather 12.1**

**Wgrib2**: 
the plugin has been built in MacOS Big Sur, Windows 11 and Ubuntu 20.04 LTS.
Wgrib uses cygwin on windows, the .dll is provided on the
bin folder and there's no need to install it.

## Installation
Copy the zip file contents to your X-Plane/Resources/plugins/PythonPlugins folder.
The resulting installation should look like:

    X-Plane/Resources/plugins/PythonPlugins/noaaweather/
    X-Plane/Resources/plugins/PythonPlugins/PI_noaaWeather.py

Please delete completely (not overwrite) any previous release of NOAA plugin in PythonPlugins folder.

## RESOURCES

### NOAA:
GFS Products:     http://www.nco.ncep.noaa.gov/pmb/products/gfs/
GFS Inventory:    http://www.nco.ncep.noaa.gov/pmb/products/gfs/gfs.t00z.pgrb2f06.shtml
WAFS Inventory:   http://www.nco.ncep.noaa.gov/pmb/products/gfs/WAFS_blended_2012010606f06.grib2.shtml

NOMADS filter: http://nomads.ncep.noaa.gov/
wgrib2:        http://www.cpc.ncep.noaa.gov/products/wesley/wgrib2/

### OpenGrADS:
Interactive desktop tool for easy access, manipulation, and visualization of
earth science data and wgrib2 builds for diverse platforms.
url:           http://sourceforge.net/projects/opengrads/


### X-Plane:
Datarefs:      https://developer.x-plane.com/datarefs/

Some info on what x-plane does with metar data:
http://code.google.com/p/fjccuniversalfmc/wiki/Winds
