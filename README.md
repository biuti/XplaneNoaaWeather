===========================================
[XPGFS] Xplane NOAA Global Forecast weather
===========================================

I created a new version of NOAAWeather originally created by [Joan](https://github.com/joanpc) and ported to python3 by [pbuckner](https://github.com/pbuckner).

You can find original version [here](http://x-plane.joanpc.com/plugins/xpgfs-noaa-weather).

And here the version of [pbuckner](https://github.com/pbuckner) ported to Python 3.x:
https://github.com/pbuckner/XplaneNoaaWeather

**You cannot have both python 3 and python 2 interfaces and plugins!**

============
Features
============

Downloads METAR and Forecast data from NOAA servers and sets x-plane weather
using forecasted and reported data for the current time and world coordinates.

- Updated winds and clouds layers
- Changes ISA atmosphere using GFS Tropo limit data
- Changes runway friction based on weather conditions
- Adds uplift and wind shear effect in stormy conditions
- Convection "bumpiness" using thermal XPlane parameters in instable conditions
- Added a surface layer to get correct ATIS informations (nearest METAR)
- Added a 'Optimised Redraw' mode to minimise clouds disappearing and recalculation, especially at cruise level or when there is an OVC layer

============
Requirements
============
- MacOS 10.14, Windows 7 and Linux kernel 4.0 and above
- X-Plane 11 and above
- pbuckner's XPPython3 plugin:
https://xppython3.readthedocs.io/en/latest/index.html
- Python 3.6 and above:
http://www.python.org/getit/

**You need to download correct XPPython3 version according to your Python3 installed version!
Read instructions.**

**Wgrib2**: 
the plugin has been built in MacOS Big Sur, Windows 11 and Ubuntu 20.04 LTS.
Wgrib uses cygwin on windows, the .dll is provided on the
bin folder and there's no need to install it.

============
Installation
============

Copy the zip file contents to your X-Plane/Resources/plugins/PythonPlugins folder.
The resulting installation should look like:

    X-Plane/Resources/plugins/PythonPlugins/noaweather/
    X-Plane/Resources/plugins/PythonPlugins/PI_noaaWeather.py

=========
RESOURCES
=========

NOAA:
-----
GFS Products:     http://www.nco.ncep.noaa.gov/pmb/products/gfs/
GFS Inventory:    http://www.nco.ncep.noaa.gov/pmb/products/gfs/gfs.t00z.pgrb2f06.shtml
WAFS Inventory:   http://www.nco.ncep.noaa.gov/pmb/products/gfs/WAFS_blended_2012010606f06.grib2.shtml

NOMADS filter: http://nomads.ncep.noaa.gov/
wgrib2:        http://www.cpc.ncep.noaa.gov/products/wesley/wgrib2/

OpenGrADS:
----------
Interactive desktop tool for easy access, manipulation, and visualization of
earth science data and wgrib2 builds for diverse platforms.
url:           http://sourceforge.net/projects/opengrads/


XPlane:
-------
datarefs:      https://developer.x-plane.com/datarefs/

Some info on what x-plane does with metar data:
               http://code.google.com/p/fjccuniversalfmc/wiki/Winds
