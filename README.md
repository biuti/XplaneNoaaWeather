===========================================
[XPGFS] Xplane NOAA Global Forecast weather
===========================================

I created a new version of NOAAWeather by [Joan](https://github.com/joanpc) and ported to python3 by [pbuckner](https://github.com/pbuckner).

You can find original version [here](http://x-plane.joanpc.com/plugins/xpgfs-noaa-weather).

And here the version of [pbuckner](https://github.com/pbuckner) ported to Python 3.x:
https://github.com/pbuckner/XplaneNoaaWeather

============
Features
============

Downloads METAR and Forecast data from NOAA servers and sets x-plane weather
using forecasted and reported data for the current time and world coordinates.

- Updated winds and clouds layers
- Changes ISA atmosphere using GFS data at tropo limit
- Adds also uplift and wind shear effect in stormy conditions
- Convection "bumpiness" using thermal XPlane parameters in instable conditions
- Added a surface layer to get correct ATIS informations (nearest METAR)

============
Requirements
============

pbuckner's XPPython3 plugin:
https://xppython3.readthedocs.io/en/latest/index.html

Python: 3.9
http://www.python.org/getit/

Wgrib2: the plugin comes with wgrib2 for common os like osx, win32 and
linux i686 glib2.5. Wgrib uses cygwin on windows, the .dll is provided on the
bin folder and there's no need to install-it.

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

NOOA:
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
datarefs:      http://www.xsquawkbox.net/xpsdk/docs/DataRefs.html

Some info on what x-plane does with metar data:
               http://code.google.com/p/fjccuniversalfmc/wiki/Winds
