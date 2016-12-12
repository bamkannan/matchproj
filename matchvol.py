import numpy as np
import pyproj
import pandas as pd
import glob
import re
import os
import datetime
import pyart  # Preload for child's module
from numpy import sqrt, cos, sin, tan, pi, exp

# Custom modules
import reflectivity_conversion
from read_gpm import read_gpm
from read_radar import read_radar
from ground_radar import *
from satellite import *
from util_fun import *

""" SECTION of user-defined parameters """
l_write = 1    # Switch for writing out volume-matched data
l_cband = 1    # Switch for C-band GR
l_netcdf = 1   # Switch for NetCDF GR data
l_dbz = 0      # Switch for averaging in dBZ
l_dp = 1       # Switch for dual-pol data
l_gpm = 1      # Switch for GPM PR data

# Start and end dates
date1 = '20150211'
date2 = '20150211'

# Set the data directories
raddir = '/g/ns/cw/arm/data-1/vlouf/cpol_season_1415'
satdir = '/data/vlouf/GPM_DATA'

# Algorithm parameters and thresholds
rmin = 15000.  # minimum GR range (m)
rmax = 150000  # maximum GR range (m)
minprof = 10   # minimum number of PR profiles with precip
maxdt = 300.   # maximum PR-GR time difference (s)
tscan = 90.    # approx. time to do first few tilts (s)
minrefg = 0.   # minimum GR reflectivity
minrefp = 18.  # minimum PR reflectivity
minpair = 10   # minimum number of paired samples
""" End of the section for user-defined parameters """

if l_gpm == 0:
    satstr = 'trmm'
    raise ValueError("TRMM not yet implemented")
else:
    satstr = 'gpm'

# Ground radar parameters
GR_param = ground_radar_params('CPOL')
rid = GR_param['rid']
lon0 = GR_param['lon0']
lat0 = GR_param['lat0']
z0 = GR_param['z0']
bwr = GR_param['bwr']

sat_params = satellite_params(satstr)
zt = sat_params['zt']
drt = sat_params['drt']
bwt = sat_params['bwt']

# Output directory
# if l_dbz == 0:
#     outdir = raddir + '/' + rid + '/' + satstr + '_comp'
# else:
#     outdir = raddir + '/' + rid + '/' + satstr + '_comp_dbz'
#
# outdir = outdir + '_new'

# Initialise error counters
ntot = 0
nerr = np.zeros((8,), dtype=int)

# Map Projection
# Options: projection transverse mercator, lon and lat of radar, and ellipsoid
# WGS84
smap = pyproj.Proj('+proj=tmerc +lon_0=131.0440 +lat_0=-12.2490 +ellps=WGS84')

# Note the lon,lat limits of the domain
xmin = -1*rmax
xmax = rmax
ymin = -1*rmax
ymax = rmax
lonmin, latmin = smap(xmin, ymin, inverse=True)  # Unused
lonmax, latmax = smap(xmax, ymax, inverse=True)  # Unused

# Gaussian radius of curvatur for the radar's position
ae = radar_gaussian_curve(lat0)

# Determine the Julian days to loop over
jul1 = datetime.datetime.strptime(date1, '%Y%m%d')
jul2 = datetime.datetime.strptime(date2, '%Y%m%d')
nday = jul2-jul1

# Date loop
for the_date in pd.date_range(jul1, jul2):
    year = the_date.year
    month = the_date.month
    day = the_date.day
    date = "%i%02i%02i" % (year, month, day)

    # Note the Julian day corresponding to 00 UTC
    jul0 = datetime.datetime(year, month, day, 0, 0, 0)

    # Note the number of satellite overpasses on this day
    satfiles = glob.glob(satdir + '/*' + date + '*.HDF5')
    nswath = len(satfiles)

    if nswath == 0:
        print('No satellite swaths')
        nerr[0] += 1
        continue

    # File loop
    for the_file in satfiles:
        ntot += 1
        orbit = get_orbit_number(the_file)

        print("Orbit " + orbit + " -- " + jul0.strftime("%d %B %Y"))

        sat = read_gpm(the_file)
        if sat is None:
            print('Bad satellite data')
            continue

        nscan = sat['nscan']
        nray = sat['nray']
        nbin = sat['nbin']
        yearp = sat['year']
        monthp = sat['month']
        dayp = sat['day']
        hourp = sat['hour']
        minutep = sat['minute']
        secondp = sat['second']
        lonp = sat['lon']
        latp = sat['lat']
        pflag = sat['pflag']
        ptype = sat['ptype']
        zbb = sat['zbb']
        bbwidth = sat['bbwidth']
        sfc = sat['sfc']
        quality = sat['quality']
        refp = sat['refl']

        # Convert to Cartesian coordinates
        res = smap(lonp, latp)
        xp = res[0]
        yp = res[1]

        # Identify profiles withing the domnain
        ioverx, iovery = np.where((xp >= xmin) & (xp <= xmax) &
                                  (yp >= ymin) & (yp <= ymax))

        if len(ioverx) == 0:
            nerr[1] += 1
            print("Insufficient satellite rays in domain.")
            continue

        # Note the first and last scan indices
        i1x, i1y = np.min(ioverx), np.min(iovery)
        i2x, i2y = np.max(ioverx), np.max(iovery)

        # Identify the coordinates of these points
        xf = xp[i1x:i2x]
        yf = yp[i1y:i2y]

        # Determine the date and time (in seconds since the start of the day)
        # of the closest approach of TRMM to the GR
        xc = xp[:, 24]  # Grid center
        yc = yp[:, 24]
        dc = sqrt(xc**2 + yc**2)
        iclose = np.argmin(dc)

        year = yearp[iclose]
        month = monthp[iclose]
        day = dayp[iclose]
        hour = hourp[iclose]
        minute = minutep[iclose]
        second = secondp[iclose]

        date = "%i%02i%02i" % (year, month, day)
        timep = "%02i%02i%02i" % (hour, minute, second)
        dtime_sat = datetime.datetime(year, month, day, hour, minute, second)
        # dtime_sat corresponds to the julp/tp stuff in the IDL code

        # Compute the distance of every ray to the radar
        d = sqrt(xp**2 + yp**2)

        # Identify precipitating profiles within the radaar range limits
        iscan, iray = np.where((d >= rmin) & (d <= rmax) & (pflag == 2))
        nprof = len(iscan)
        if nprof < minprof:
            nerr[2] += 1
            print('Insufficient precipitating satellite rays in domain', nprof)

        # Note the scan and ray indices for these rays
        # iscan, iray = np.unravel_index(iprof, d.shape)

        # Extract data for these rays
        xp = xp[iscan, iray]
        yp = yp[iscan, iray]
        xc = xc[iscan]
        yc = yc[iscan]
        ptype = ptype[iscan, iray]
        zbb = zbb[iscan, iray]
        bbwidth = bbwidth[iscan, iray]
        sfc = sfc[iscan, iray]
        quality = quality[iscan, iray]

        tmp = np.zeros((nprof, nbin), dtype=float)
        for k in range(0, nbin):
            tmp[:, k] = (refp[:, :, k])[iscan, iray]

        refp = tmp

        # Note the scan angle for each ray
        alpha = np.abs(-17.04 + np.arange(nray)*0.71)
        alpha = alpha[iray]

        # Remember Python's ways: unlike IDL, rebin cannot change the number
        # of dimension. the_range dimension is equal to nbin, and we nw wnat
        # to copy it for nprof x nbin
        the_range_1d = np.arange(nbin)*drt
        the_range = np.zeros((nprof, nbin))
        for idx in range(0, nprof):
            the_range[idx, :] = the_range_1d[:]

        xp, yp, zp, ds, the_alpha = correct_parallax(xc, yc, xp, yp, alpha, the_range)
        alpha = the_alpha


        if len(ds) == 0:
            continue
        if np.min(ds) < 0:
            continue

        # Compute the (approximate) volume of each PR bin
        rt = zt/cos(pi/180*alpha) - the_range
        volp = drt*(1.e-9)*pi*(rt*pi/180*bwt/2.)**2

        # Compute the ground-radar coordinates of the PR pixels
        sp = sqrt(xp**2 + yp**2)
        gamma = sp/ae
        ep = 180/pi*np.arctan((cos(gamma) - (ae + z0)/(ae + zp))/sin(gamma))
        # rp = (ae + zp)*sin(gamma)/cos(pi/180*ep)  # Not used
        # ap = 90-180/pi*np.arctan2(yp, xp)  # Shape (nprof x nbin)  # Not used

        # Determine the median brightband height
        # 1D arrays
        ibb = np.where((zbb > 0) & (bbwidth > 0) & (quality == 1))[0]
        nbb = len(ibb)
        if nbb >= minprof:
            zbb = np.median(zbb[ibb])
            bbwidth = np.median(bbwidth[ibb])
        else:
            nerr[3] += 1
            print('Insufficient bright band rays', nbb)
            continue

        # Set all values less than minrefp as missing
        ibadx, ibady = np.where(refp < minrefp)  # WHERE(refp lt minrefp,nbad)
        if len(ibadx) > 0:
            refp[ibadx, ibady] = np.NaN

        # Convert to S-band using method of Cao et al. (2013)
        if l_cband:
            refp_ss, refp_sh = reflectivity_conversion.convert_to_Cband(refp, zp, zbb, bbwidth)
        else:
            refp_ss, refp_sh = reflectivity_conversion.convert_to_Sband(refp, zp, zbb, bbwidth)

        # Get the ground radar file lists (next 20 lines can be a function)
        radar_file_list = get_files(raddir + '/' + date + '/')

        # Get the datetime for each radar files
        dtime_radar = [None]*len(radar_file_list)  # Allocate empty list
        for cnt, radfile in enumerate(radar_file_list):
            dtime_radar[cnt] = get_time_from_filename(radfile, date)

        # Find the nearest scan time
        closest_dtime_rad = get_closest_date(dtime_radar, dtime_sat)

        if dtime_sat >= closest_dtime_rad:
            time_difference = dtime_sat - closest_dtime_rad
        else:
            time_difference = closest_dtime_rad - dtime_sat

        # Looking at the time difference between satellite and radar
        if time_difference.seconds > maxdt:
            print('Time difference is of %i.' % (time_difference.seconds))
            print('This time difference is bigger' +
                  ' than the acceptable value of ', maxdt)
            nerr[5] += 1
            continue  # To the next satellite file

        # Radar file corresponding to the nearest scan time
        radfile = get_filename_from_date(radar_file_list, closest_dtime_rad)
        time = closest_dtime_rad  # Keeping the IDL program notation

        radar = read_radar(radfile)

        ngate = radar['ngate']
        nbeam = radar['nbeam']
        ntilt = radar['ntilt']
        r_range = radar['range']
        azang = radar['azang']
        elang = radar['elang']
        dr = radar['dr']
        refg = radar['reflec']

        # Determine the Cartesian coordinates of the ground radar's pixels
        rg, ag, eg = np.meshgrid(r_range, azang, elang, indexing='ij')
        zg = sqrt(rg**2 + (ae + z0)**2 + 2*rg*(ae + z0)*sin(pi/180*eg)) - ae
        # ae is the gaussian curve
        sg = ae*np.arcsin(rg*cos(pi/180*eg)/(ae + zg))
        xg = sg*cos(pi/180*(90 - ag))
        yg = sg*sin(pi/180*(90 - ag))

        # Compute the volume of each radar bin
        volg = 1e-9*pi*dr*(pi/180*bwr/2*rg)**2

        #  Set all values less than minref as missing
        rbad, azbad, elbad = np.where(refg < minrefg)
        refg[rbad, azbad, elbad] = np.NaN

        # Convert S-band GR reflectivities to Ku-band
        refg_ku = reflectivity_conversion.convert_to_Ku(refg, zg, zbb, l_cband)

        # Create arrays to store comparison variables
        '''Coordinates'''
        x = np.zeros((nprof, ntilt))  # x coordinate of sample
        y = np.zeros((nprof, ntilt))  # y coordinate of sample
        z = np.zeros((nprof, ntilt))  # z coordinate of sample
        dz = np.zeros((nprof, ntilt))  # depth of sample
        ds = np.zeros((nprof, ntilt))  # width of sample
        r = np.zeros((nprof, ntilt))  # range of sample from ground radar

        '''Reflectivities'''
        ref1 = np.zeros((nprof, ntilt)) + np.NaN  # PR reflectivity
        ref2 = np.zeros((nprof, ntilt)) + np.NaN  # PR reflec S-band, snow
        ref3 = np.zeros((nprof, ntilt)) + np.NaN  # PR reflec S-band, hail
        ref4 = np.zeros((nprof, ntilt)) + np.NaN  # GR reflectivity
        ref5 = np.zeros((nprof, ntilt)) + np.NaN  # GR reflectivity Ku-band
        iref1 = np.zeros((nprof, ntilt)) + np.NaN  # path-integrated PR reflec
        iref2 = np.zeros((nprof, ntilt)) + np.NaN  # path-integrated GR reflec
        stdv1 = np.zeros((nprof, ntilt)) + np.NaN  # STD of PR reflectivity
        stdv2 = np.zeros((nprof, ntilt)) + np.NaN  # STD of GR reflectivity

        '''Number of bins in sample'''
        ntot1 = np.zeros((nprof, ntilt), dtype=int)  # Total nb of PR bin in sample
        nrej1 = np.zeros((nprof, ntilt), dtype=int)  # Nb of rejected PR bin in sample
        ntot2 = np.zeros((nprof, ntilt), dtype=int)  # Total nb of GR bin in sample
        nrej2 = np.zeros((nprof, ntilt), dtype=int)  # Nb of rejected GR bin in sample
        vol1 = np.zeros((nprof, ntilt)) + np.NaN  # Total volume of PR bins in sample
        vol2 = np.zeros((nprof, ntilt)) + np.NaN  # Total volume of GR bins in sample

        # Compute the path-integrated reflectivities at every points
        nat_refp = 10**(refp/10.0)  # In natural units
        nat_refg = 10**(refg/10.0)
        irefp = np.fliplr(nancumsum(np.fliplr(nat_refp), 1))
        irefg = nancumsum(nat_refg)
        irefp = drt*(irefp - nat_refp/2)
        irefg = dr*(irefg - nat_refg/2)
        irefp = 10*np.log10(irefp)
        irefg = 10*np.log10(irefg)

        # Convert to linear units
        if l_dbz == 0:
            refp = 10**(refp/10.0)
            refg = 10**(refg/10.0)
            refp_ss = 10**(refp_ss/10.0)
            refp_sh = 10**(refp_sh/10.0)
            refg_ku = 10**(refg_ku/10.0)

        irefp = 10**(irefp/10.0)
        irefg = 10**(irefg/10.0)

        # Loop over the TRMM profiles
        for ii in range(0, nprof):

            # Loop over the GR elevation scan
            for jj in range(0, ntilt):

                # Identify those PR bins which fall within the GR sweep
                ip = np.where((ep[ii, :] >= elang[jj] - bwr/2) &
                              (ep[ii, :] <= elang[jj] + bwr/2))

                # Store the number of bins
                ntot1[ii, jj] = len(ip)

                if len(ip) == 0:
                    continue

                x[ii, jj] = np.mean(xp[ii, ip])
                y[ii, jj] = np.mean(yp[ii, ip])
                z[ii, jj] = np.mean(zp[ii, ip])

                # Compute the thickness of the layer
                nip = len(ip)
                dz[ii, jj] = nip*drt*cos(pi/180*alpha[ii, 0])

                # Compute the PR averaging volume
                vol1[ii, jj] = np.sum(volp[ii, ip])

                # Note the mean TRMM beam diameter
                ds[ii, jj] = pi/180*bwt*np.mean((zt - zp[ii, ip])/cos(pi/180*alpha[ii, ip]))

                # Note the radar range
                s = sqrt(x[ii, jj]**2 + y[ii, jj]**2)
                r[ii, jj] = (ae + z[ii, jj])*sin(s/ae)/cos(pi/180*elang[jj])

                # Check that sample is within radar range
                if r[ii, jj] + ds[ii, jj]/2 > rmax:
                    continue

                # Extract the relevant PR data
                refp1 = refp[ii, ip].flatten()
                refp2 = refp_ss[ii, ip].flatten()
                refp3 = refp_sh[ii, ip].flatten()
                irefp1 = irefp[ii, ip].flatten()

                # Average over those bins that exceed the reflectivity
                # threshold (linear average)

                ref1[ii, jj] = np.nanmean(refp1)
                ref2[ii, jj] = np.nanmean(refp2)
                ref3[ii, jj] = np.nanmean(refp3)
                iref1[ii, jj] = np.nanmean(irefp1)

                if l_dbz == 0:
                    stdv1[ii, jj] = np.nanstd(10*np.log10(refp1))
                else:
                    stdv1[ii, jj] = np.nanstd(refp1)

                # Note the number of rejected bins
                nrej1[ii, jj] = int(np.sum(np.isnan(refp1)))

                if ~np.isnan(stdv1[ii, jj]) and nip - nrej1[ii, jj] > 1:
                    continue

                # Compute the horizontal distance to all the GR bins
                d = sqrt((xg[:, :, jj] - x[ii, jj])**2 + (yg[:, :, jj] - y[ii, jj])**2)

                # Find all GR bins within the SR beam
                igx, igy = np.where(d <= ds[ii, jj]/2)

                # Store the number of bins
                ntot2[ii, jj] = len(igx)

                if len(igx) == 0:
                    continue

                # Extract the relevant GR data
                refg1 = refg[:, :, jj][igx, igy].flatten()
                refg2 = refg_ku[:, :, jj][igx, igy].flatten()
                volg1 = volg[:, :, jj][igx, igy].flatten()
                irefg1 = irefg[:, :, jj][igx, igy].flatten()

                #  Comupte the GR averaging volume
                vol2[ii, jj] = np.sum(volg1)

                # Average over those bins that exceed the reflectivity
                # threshold (exponential distance and volume weighting)
                w = volg1*exp(-1*(d[igx, igy]/(ds[ii, jj]/2.))**2)
                w = w*refg1/refg2

                ref2[ii, jj] = np.nansum(w*refg1)/np.nansum(w)
                ref5[ii, jj] = np.nansum(w*refg2)/np.nansum(w)
                iref2[ii, jj] = np.nansum(w*irefg1)/np.nansum(w)

                if l_dbz == 0:
                    stdv2[ii, jj] = np.nanstd(10*np.log10(refg1))
                else:
                    stdv2[ii, jj] = np.nanstd(refg1)

                # Note the number of rejected bins
                nrej2[ii, jj] = int(np.sum(np.isnan(refg1)))

            # END FOR
        # END FOR

        # Correct std
        stdv1[np.isnan(stdv1)] = 0
        stdv2[np.isnan(stdv2)] = 0

        # Convert back to dBZ
        iref1 = 10*np.log10(iref1)
        iref2 = 10*np.log10(iref2)
        if l_dbz == 0:
            refp = 10*np.log10(refp)
            refg = 10*np.log10(refg)
            refp_ss = 10*np.log10(refp_ss)
            refp_sh = 10*np.log10(refp_sh)
            refg_ku = 10*np.log10(refg_ku)
            ref1 = 10*np.log10(ref1)
            ref2 = 10*np.log10(ref2)
            ref3 = 10*np.log10(ref3)
            ref4 = 10*np.log10(ref4)
            ref5 = 10*np.log10(ref5)

        # Extract comparison pairs
        ipairx, ipairy = np.where((~np.isnan(ref1)) & (~np.isnan(ref2)))
        if len(ipairx) < minpair:
            nerr[7] += 1
            print('Insufficient comparison pairs')
            continue

        iprof = ipairx
        itilt = ipairy

        match_vol = dict()

        match_vol['x'] = x[ipairx, ipairy]
        match_vol['y'] = y[ipairx, ipairy]
        match_vol['z'] = z[ipairx, ipairy]
        match_vol['dz'] = dz[ipairx, ipairy]
        match_vol['ds'] = ds[ipairx, ipairy]
        match_vol['r'] = r[ipairx, ipairy]

        match_vol['el'] = elang[itilt]

        match_vol['dt'] = time_difference.seconds  # TODO CHECK!

        match_vol['ref1'] = ref1[ipairx, ipairy]
        match_vol['ref2'] = ref2[ipairx, ipairy]
        match_vol['ref3'] = ref3[ipairx, ipairy]
        match_vol['ref4'] = ref4[ipairx, ipairy]
        match_vol['ref5'] = ref5[ipairx, ipairy]
        match_vol['iref1'] = iref1[ipairx, ipairy]
        match_vol['iref2'] = iref2[ipairx, ipairy]
        match_vol['ntot1'] = ntot1[ipairx, ipairy]
        match_vol['nrej1'] = nrej1[ipairx, ipairy]
        match_vol['ntot2'] = ntot2[ipairx, ipairy]
        match_vol['nrej2'] = nrej2[ipairx, ipairy]

        match_vol['sfc'] = sfc[iprof]
        match_vol['ptype'] = ptype[iprof]
        match_vol['iray'] = iray[iprof]
        match_vol['iscan'] = iscan[iprof]

        match_vol['stdv1'] = stdv1[ipairx, ipairy]
        match_vol['stdv2'] = stdv2[ipairx, ipairy]
        match_vol['vol1'] = vol1[ipairx, ipairy]
        match_vol['vol2'] = vol2[ipairx, ipairy]

        out_name = "RID_" + rid + "_ORBIT_" + orbit + "_DATE_" + jul0.strftime("%Y%m%d")

        save_data(out_name, match_vol)
