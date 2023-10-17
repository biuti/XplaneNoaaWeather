# [XPGFS] NOAA Global Weather (Xplane 12)
I created a new version of NOAAWeather originally created by [Joan](https://github.com/joanpc) and ported to python3 by [pbuckner](https://github.com/pbuckner).

You can find original version [here](http://x-plane.joanpc.com/plugins/xpgfs-noaa-weather) (I guess it will not work in XP12).

And here the version of [pbuckner](https://github.com/pbuckner) ported to Python 3.x:
https://github.com/pbuckner/XplaneNoaaWeather

> [!IMPORTANT]
> You cannot have both python 3 and python 2 interfaces and plugins!

## Features
At this stage the plugin in its X-Plane 12 version is almost only monitoring what XP Real Weather engine is doing.

As it already takes data from GFS Grib files, probably there will be no need to download them anylonger.

As soon as a final version of X-Plane 12 will be released, I will consider if some of the XP11 version features will still be needed.

- Writes missing METAR.rwx file for compatibility with XP11
- Ability to populate METAR.rwx file using XP12 Real Weather as data source

> [!NOTE]
> latest versions of AviTab do not need METAR.rwx file anymore, so this function is disabled by default and probably deprecated in next versions (at the moment the API still has some problems, so this plugin is still more reliable to retrieve METAR)

- monitors XP12 real weather behavior
- METAR query window that displays both from XP12 Real Weather and chosen source (NOAA, IVAO or VATSIM servers)


> [!WARNING]
> Be aware that using XP12 Real Weather as data source when flying online could give you outdated information.
This means that ATC information, ATIS, runway in use and generally weather for any pilot not using XP12 could be different from what you expect looking at your data.
Lately (XP12.05r1) Real Weather METAR is a lot improved compared to previous versions and almost always up-to-date. 
Anyway, be respectful of other users, you are the one using wrong data.

## Requirements
- MacOS 10.14, Windows 7 and Linux kernel 4.0 and above
- X-Plane 12.05b2 and above (not tested with previous versions) 
- pbuckner's XPPython3 plugin:
https://xppython3.readthedocs.io/en/latest/index.html
- Python 3.10 and above:
http://www.python.org/getit/

> [!IMPORTANT]
> You need to download correct XPPython3 version according to your Python3 installed version!
Read instructions.**

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
