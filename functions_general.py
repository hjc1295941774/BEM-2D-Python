import os
import sys
import numpy as np
from scipy import array
from scipy.interpolate import PchipInterpolator, splrep

    # x,z components of each panel's tangential and normal vectors
def panel_vectors(x,z):
    """
    Calculates the normal and tangential unit vectors for 2D body panel.
    
    Args:
        x (float: 1 x N): x panel endpoint coordinates
        z (float: 1 x N): z panel endpoint coordinates
        
    Returns:
        tx (float: 1 x N-1): x unit vector component of the panel's tangent vector
        tz (float: 1 x N-1): z unit vector component of the panel's tangent vector
        nx (float: 1 x N-1): x unit vector component of the panel's normal vector
        nz (float: 1 x N-1): z unit vector component of the panel's normal vector
        lpanel (float: 1 x N-1): length of each panel
    """
    lpanel = np.sqrt((x[1:]-x[:-1])**2 + (z[1:]-z[:-1])**2)
    tx = (x[1:]-x[:-1])/lpanel
    tz = (z[1:]-z[:-1])/lpanel
    nx = -tz
    nz = tx
    return (tx,tz,nx,nz,lpanel)

    # x,z components of each midpoint's/collocation point's tangential and normal vectors
def point_vectors(xdp,xdm,zdp,zdm):
    tx = (xdp-xdm)/np.sqrt((xdp-xdm)**2 + (zdp-zdm)**2)
    tz = (zdp-zdm)/np.sqrt((xdp-xdm)**2 + (zdp-zdm)**2)
    nx = -tz
    nz = tx
    return(tx,tz,nx,nz)

def archive(array, axis=0):
    """
    Shifts array values along an axis (row-wise by default).

    Used for arrays that keep past values for differencing with respect to time.

    Args:
        array: The array that will be manipulated.
        axis: The axis to shift values along (0==row-wise, 1==column-wise).
    """
    if len(np.shape(array)) == 1:
        array[1:] = array[:-1]
    elif len(np.shape(array)) == 2:
        if axis == 0:
            array[1:,:] = array[:-1,:]
        else:
            array[:,1:] = array[:,:-1]

# Velocity and velocity potential equations are defined in panel coordinates so a transformation should be done
# Each row of xp1/xp2/zp is a target, and each column is an influence
# NI is N influences, NT is N targets
# xi/zi is x/z of influences, xt/zt is x/z of target points
def transformation(xt,zt,xi,zi):
# Returns xp1, xp2, zp
# Others: NT, NI, tx, tz, nx, nz, dummy, x_tile, z_tile, tx_tile, tz_tile

    NT = np.size(xt)
    NI = np.size(xi)-1

    (tx,tz,nx,nz) = panel_vectors(xi,zi)[:-1]

    # Intermediary variables to reduce number of tile/repeat operations
    # From normalvectors: tx==nz, tz==-nx
    x_tile = np.repeat(xt[:,np.newaxis],NI,1) - np.repeat(xi[:-1,np.newaxis].T,NT,0)
    z_tile = np.repeat(zt[:,np.newaxis],NI,1) - np.repeat(zi[:-1,np.newaxis].T,NT,0)
    tx_tile = np.repeat(tx[:,np.newaxis].T,NT,0)
    tz_tile = np.repeat(tz[:,np.newaxis].T,NT,0)

    # Transforming left side collocation points from global to local coordinates
    xp1 = x_tile*tx_tile + z_tile*tz_tile
    zp = x_tile*(-tz_tile) + z_tile*tx_tile

    # Transforming right side panel points into local coordinate system
    dummy = (xi[1:]-xi[:-1])*tx + (zi[1:]-zi[:-1])*tz
    xp2 = xp1 - np.repeat(dummy[:,np.newaxis].T,NT,0)

    return(xp1,xp2,zp)

def absoluteToBody(Body, Solid, THETA, HEAVE):
    """Transforms absolute reference frame to body reference frame"""
    Body.BF.x = ((Body.AF.x - Body.AF.x_le) * np.cos(-1*THETA) - (Body.AF.z - Body.AF.z_le) * np.sin(-1*THETA))
    Body.BF.z = ((Body.AF.z - Body.AF.z_le) * np.cos(-1*THETA) + (Body.AF.x - Body.AF.x_le) * np.sin(-1*THETA))
    Body.BF.x_col = ((Body.BF.x[1:] + Body.BF.x[:-1])/2)
    Body.BF.z_col = ((Body.BF.z[1:] + Body.BF.z[:-1])/2)

    Solid.nodesNew[:,0] = (Solid.nodes[:,0] - Body.AF.x_le) * np.cos(-1*THETA) - (Solid.nodes[:,1] - Body.AF.z_le) * np.sin(-1*THETA)
    Solid.nodesNew[:,1] = (Solid.nodes[:,1] - Body.AF.z_le) * np.cos(-1*THETA) + (Solid.nodes[:,0] - Body.AF.x_le) * np.sin(-1*THETA)

def ramp(t, slope, startTime):
    """
    This function can generate a ramp signal based on the following inputs:

    Args:
        t: array of time samples
        slope: slope of the ramp signal
        startTime: location where the ramp turns on
    """
    # Get the number of samples in the output signal
    N = t.size

    # Initialize the ramp signal
    r = np.zeros(N)

    # Find the index where the ramp turns on
    if (np.median(np.diff(t)) > 0):
        startInd = np.min((t>=startTime).nonzero())
        popInd =np.arange(startInd,N)
    elif (np.median(np.diff(t)) < 0):
        # Time-reversed ramp
        startTime = -1. * startTime
        startInd = np.max((t>=startTime).nonzero())
        popInd = np.arange(startInd)
        slope = -1. * slope

    # For indicies greater than the start time, compute the
    # proper signal value based on slope
    r[popInd] = slope * (t[popInd] + startTime) - 2 * startTime * slope

    return (r)

def geom_setup(P, PC, Swimmer, solid=None, FSI=None, PyFEA=None):
    SwiP     = [None for x in xrange(P['N_SWIMMERS'])]
    GeoP     = [None for x in xrange(P['N_SWIMMERS'])]
    MotP     = [None for x in xrange(P['N_SWIMMERS'])]
    Swimmers = [None for x in xrange(P['N_SWIMMERS'])]
    SolidP   = [None for x in xrange(P['N_SWIMMERS'])]
    FSIP     = [None for x in xrange(P['N_SWIMMERS'])]
    PyFEAP   = [None for x in xrange(P['N_SWIMMERS'])]

    for i in xrange(P['N_SWIMMERS']):
        SwiP[i] = PC.SwimmerParameters(P['CE'], P['DELTA_CORE'], P['SW_KUTTA'])
        if (P['SW_GEOMETRY'] == 'FP'):
            GeoP[i] = PC.GeoFPParameters(P['N_BODY'], P['S'], P['C'], P['T_MAX'])
        elif (P['SW_GEOMETRY'] == 'TD'):
            GeoP[i] = PC.GeoTDParameters(P['N_BODY'], P['S'], P['C'], P['T_MAX'])
        elif (P['SW_GEOMETRY'] == 'VDV'):
            GeoP[i] = PC.GeoVDVParameters(P['N_BODY'], P['S'], P['C'], P['K'], P['EPSILON'])
        else:
            print 'ERROR! Invalid geometry type.'

        MotP[i] = PC.MotionParameters(P['X_START'][i], P['Z_START'][i], P['V0'], P['THETA_MAX'], P['F'], P['PHI'])

        Swimmers[i] = Swimmer(SwiP[i], GeoP[i], MotP[i], P['COUNTER']-1)

        if (P['SW_FSI'] == True):
            SolidP[i] = solid(Swimmers[i].Body, P['N_ELEMENTS_S'], P['T_MAX'])
            FSIP[i] = FSI(Swimmers[i].Body, SolidP[i])
            PyFEAP[i] = PyFEA(SolidP[i], P['SW_SPRING'], P['FRAC_DELT'], P['DEL_T'], P['E'], P['RHO_S'])

            SolidP[i].initMesh()
            if (P['SW_GEOMETRY'] == 'FP'):
                SolidP[i].initThinPlate(P['T_MAX'],P['C'],P['SW_CNST_THK_BM'],P['T_CONST'],P['FLEX_RATIO'])
            elif (P['SW_GEOMETRY'] == 'TD'):
                SolidP[i].initTearDrop(P['T_MAX'],P['C'],P['SW_CNST_THK_BM'],P['T_CONST'],P['FLEX_RATIO'])
            else:
                print 'ERROR! Invalid geometry type.'

    return (SwiP, GeoP, MotP, Swimmers, SolidP, FSIP, PyFEAP)

def simulation_startup(P, DIO, PC, Swimmer, solid=None, FSI=None, PyFEA=None):
    if (os.path.exists(P['OUTPUT_DIR']) == False or os.listdir(P['OUTPUT_DIR']) == []):
        P['START_FROM'] = 'zeroTime'

    if (P['START_FROM'] == 'latestTime'):
        startTime = 0.
        for file in os.listdir(''.join((P['OUTPUT_DIR'], '/'))):
            startTime = max(float(file), startTime)

        (sP, i, FLOWTIME, SwiP, GeoP, MotP, Swimmers, SolidP, FSIP, PyFEAP) = DIO.read_data(''.join((P['OUTPUT_DIR'], '/', '%.8f' % startTime)))
        if not (sP['DEL_T'] == P['DEL_T']) and (sP['N_SWIMMERS'] == P['N_SWIMMERS']) and (sP['N_BODY'] == P['N_BODY']):
            print 'ERROR! Inconsistent input parameters with starting data file.'

        if (Swimmers[0].Wake.x.shape[0] < P['COUNTER']):
            for Swim in Swimmers:
                Swim.Wake.x.resize(P['COUNTER'])
                Swim.Wake.z.resize(P['COUNTER'])
                Swim.Wake.mu.resize(P['COUNTER']-1)
                Swim.Wake.gamma.resize(P['COUNTER'])

        START_COUNTER = i + 1
        COUNTER = P['COUNTER']

    elif (P['START_FROM'] == 'firstTime'):
        startTime = sys.float_info.max
        for file in os.listdir(''.join((P['OUTPUT_DIR'], '/'))):
            startTime = max(float(file), startTime)

        (sP, i, FLOWTIME, SwiP, GeoP, MotP, Swimmers, SolidP, FSIP, PyFEAP) = DIO.read_data(''.join((P['OUTPUT_DIR'], '/', '%.8f' % startTime)))
        if not (sP['DEL_T'] == P['DEL_T']) and (sP['N_SWIMMERS'] == P['N_SWIMMERS']) and (sP['N_BODY'] == P['N_BODY']):
            print 'ERROR! Inconsistent input parameters with starting data file.'

        if (Swimmers[0].Wake.x.shape[0] < P['COUNTER']):
            for Swim in Swimmers:
                Swim.Wake.x.resize(P['COUNTER'])
                Swim.Wake.z.resize(P['COUNTER'])
                Swim.Wake.mu.resize(P['COUNTER']-1)
                Swim.Wake.gamma.resize(P['COUNTER'])

        START_COUNTER = i + 1
        COUNTER = P['COUNTER']

    elif (P['START_FROM'] == 'zeroTime'):
        startTime = '0.00000000'
        (SwiP, GeoP, MotP, Swimmers, SolidP, FSIP, PyFEAP) = geom_setup(P, PC, Swimmer, solid, FSI, PyFEA)

        START_COUNTER = 0
        COUNTER = P['COUNTER']

    else:
        print 'ERROR! Invalid START_FROM. Valid values are:'
        print '    latestTime'
        print '    firstTime'
        print '    zeroTime'

    return (START_COUNTER, COUNTER, SwiP, GeoP, MotP, Swimmers, SolidP, FSIP, PyFEAP)

def extrap1d(interpolator):
    xs = interpolator.x
    ys = interpolator.y

    def pointwise(x):
        if x < xs[0]:
            return ys[0]+(x-xs[0])*(ys[1]-ys[0])/(xs[1]-xs[0])
        elif x > xs[-1]:
            return ys[-1]+(x-xs[-1])*(ys[-1]-ys[-2])/(xs[-1]-xs[-2])
        else:
            return interpolator(x)

    def ufunclike(xs):
        return array(map(pointwise, array(xs)))

    return ufunclike
    
def intermittent_ref(HEAVE_MAX, THETA_MAX, phi, DC, mag):
    # Constants
    N = 5000      # Defines the number of points for the active and passive portions of the reference signal
    Tstep = 1e-5
    # Defining a smooth intermittent signal
    # Modifying the slope of the envelope signal as DC --> 1
    if (DC > 0.9):
        a = mag / (1 - DC)
    else:
        a = 10 * mag
    
    # Defining the non-dimenional time for the active and passive portions of
    # the cycle. t_T_active is the non-dimensional time over the active portion, 
    # t is normalized by the active period. t_T_passive is the non-dimensional 
    # time over the passive portion, t is normalized by the active period.
    t_T_active = np.linspace(0.0, 1.0, N).T
    if (DC < 1.0):
        t_T_passive = np.linspace(1.0, 1. / DC, N).T
#        t_T_passive = np.copy(t_T_passive[1:]) # Removing overlapping point
    else:
        t_T_passive = np.empty((N,1)) # For DC = 1 the passive time vector must be a null vector 
    
    # Creating the amplitude envelope and modifying the active portion of the
    # signal
    amp_env = -np.tanh(a * t_T_active) * np.tanh(a * (t_T_active - 1.0))
    y_pitch_sin       = THETA_MAX * np.sin(2. * np.pi * t_T_active           + DC * phi)
    y_pitch_sin_plus  = THETA_MAX * np.sin(2. * np.pi * (t_T_active + Tstep) + DC * phi)
    y_pitch_sin_minus = THETA_MAX * np.sin(2. * np.pi * (t_T_active - Tstep) + DC * phi)
    
    y_heave_sin       = HEAVE_MAX * np.sin(2. * np.pi * t_T_active          )
    y_heave_sin_plus  = HEAVE_MAX * np.sin(2. * np.pi * (t_T_active + Tstep))
    y_heave_sin_minus = HEAVE_MAX * np.sin(2. * np.pi * (t_T_active - Tstep))
    
    sig_pitch_mod       = amp_env * y_pitch_sin
    sig_pitch_mod_plus  = amp_env * y_pitch_sin_plus
    sig_pitch_mod_minus = amp_env * y_pitch_sin_minus
    
    sig_heave_mod       = amp_env * y_heave_sin
    sig_heave_mod_plus  = amp_env * y_heave_sin_plus
    sig_heave_mod_minus = amp_env * y_heave_sin_minus
    
    # Assembling an intermittent reference signal for one full cycle
    y_pitch       = np.hstack((sig_pitch_mod,       np.zeros(t_T_passive.size-1).T))
    y_pitch_plus  = np.hstack((sig_pitch_mod_plus,  np.zeros(t_T_passive.size-1).T))
    y_pitch_minus = np.hstack((sig_pitch_mod_minus, np.zeros(t_T_passive.size-1).T))
    
    y_heave       = np.hstack((sig_heave_mod,       np.zeros(t_T_passive.size-1).T))
    y_heave_plus  = np.hstack((sig_heave_mod_plus,  np.zeros(t_T_passive.size-1).T))
    y_heave_minus = np.hstack((sig_heave_mod_minus, np.zeros(t_T_passive.size-1).T))
    
    t_T = np.hstack((t_T_active, t_T_passive[1:]))
    
    return(t_T, y_pitch, y_pitch_plus, y_pitch_minus, y_heave, y_heave_plus, y_heave_minus)
    
def intermittent(HEAVE_MAX, THETA_MAX, phi, DC, f, N_STEP, N_CYC, s):
    # Constants
    mag = 3 # defines the slope of the hyperbolic tangent smoothing function inside of intermittent_ref
    
    # Defining an intermittent signal
    (t_T_ref, y_pitch_ref, y_pitch_ref_plus, y_pitch_ref_minus, y_heave_ref, 
     y_heave_ref_plus, y_heave_ref_minus) = intermittent_ref(HEAVE_MAX, THETA_MAX, phi, DC, mag)
    
    # Defining the time step spacing, delT/T_active, that is the time step
    # normalized by the period of the active motion, which is T_active = 1/f
    delT_T = 1.0 / DC / N_STEP / f
    
    # Creating time signal
    t_T_single = np.arange(0., 1. / DC / f, delT_T).T

    period = t_T_single[-1]
    t_actual = t_T_ref / f
    
    # Sampling the pitch reference signal at t_T    
    y_pitch_single       = PchipInterpolator(t_actual, y_pitch_ref      )(t_T_single)
    y_pitch_single_plus  = PchipInterpolator(t_actual, y_pitch_ref_plus )(t_T_single)
    y_pitch_single_minus = PchipInterpolator(t_actual, y_pitch_ref_minus)(t_T_single)
    
    # Sampling the heave reference signal at t_T
    y_heave_single       = PchipInterpolator(t_actual, y_heave_ref      )(t_T_single)
    y_heave_single_plus  = PchipInterpolator(t_actual, y_heave_ref_plus )(t_T_single)
    y_heave_single_minus = PchipInterpolator(t_actual, y_heave_ref_minus)(t_T_single)
    
    # Copying the signal for Ncyc cycles
    if (s == 0):
        angle_pitch = np.hstack((y_pitch_single, np.tile(y_pitch_single, (N_CYC-1)))) # Full pitch signal for intermittent swimming
    elif (s == 1):
        angle_pitch = np.hstack((y_pitch_single_plus, np.tile(y_pitch_single_plus, (N_CYC-1))))
    elif (s == -1):
        angle_pitch = np.hstack((y_pitch_single_minus, np.tile(y_pitch_single_minus, (N_CYC-1))))
    
    if (s==0):
        phase_heave = np.hstack((y_heave_single, np.tile(y_heave_single, (N_CYC-1)))) # Full heave signal for intermittent swimming
    elif (s==1):
        phase_heave = np.hstack((y_heave_single_plus, np.tile(y_heave_single_plus, (N_CYC-1))))
    elif (s==-1):
        phase_heave = np.hstack((y_heave_single_minus, np.tile(y_heave_single_minus, (N_CYC-1))))
    
    return(angle_pitch, phase_heave, period)

def multi_kinematics(P, PHI=0., scale=None, rate=50):
    delta = 1. / rate
    if (scale == None):
        x = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50]
        y = [2.1790849673, 1.5669799009, 1.3790259591, 1.2876797596, 1.2332922318, 1.1968681428, 1.1709601874, 1.1512761466, 1.136077986, 1.1238710886]
        scaleSig = PchipInterpolator(x, y)
        scale = [scaleSig(rate), scaleSig(rate), scaleSig(rate), scaleSig(rate)]
    
    # Sine Wave Input
    sineFrac      = [scale[0] * np.sin(2 * np.pi * P['F'] * P['T'][i]                + PHI) for i in xrange(P['COUNTER'])]
    sineFracMinus = [scale[0] * np.sin(2 * np.pi * P['F'] * (P['T'][i] - P['TSTEP']) + PHI) for i in xrange(P['COUNTER'])]
    sineFracPlus  = [scale[0] * np.sin(2 * np.pi * P['F'] * (P['T'][i] + P['TSTEP']) + PHI) for i in xrange(P['COUNTER'])]
    
    # Square Wave Input
#    tt = [P['DEL_T'] * i for i in xrange(P['N_STEP'] / 2 + 1)]
#    alpha = 2. + (2. / 3.)
#    A = 1.0
#    u_tip = 2. * np.pi * alpha * P['F'] * A
#    off = 0.25 * P['F']
#    a = -u_tip / 2. / off**3 + A / off**4
#    b = -2. * A / off**2 + u_tip / 2. / off
#    sqr_sig       = [a * (tt[i]              - off)**4 + b * (tt[i]              - off)**2 + A for i in xrange(P['N_STEP'] / 2 + 1)]
#    sqr_sig_minus = [a * (tt[i] - P['TSTEP'] - off)**4 + b * (tt[i] - P['TSTEP'] - off)**2 + A for i in xrange(P['N_STEP'] / 2 + 1)]
#    sqr_sig_plus  = [a * (tt[i] + P['TSTEP'] - off)**4 + b * (tt[i] + P['TSTEP'] - off)**2 + A for i in xrange(P['N_STEP'] / 2 + 1)]
#    squareFrac      = [0.]
#    squareFracMinus = [0.]
#    squareFracPlus  = [0.]
#    for j in xrange(2 * P['N_CYC']):
#        if np.mod(j+1,2) == 0:
#            squareFrac      = squareFrac      + [-sqr_sig[i+1] for i in xrange(P['N_STEP'] / 2)]
#            squareFracMinus = squareFracMinus + [-sqr_sig_minus[i+1] for i in xrange(P['N_STEP'] / 2)]
#            squareFracPlus  = squareFracPlus  + [-sqr_sig_plus[i+1] for i in xrange(P['N_STEP'] / 2)]
#        else:
#            squareFrac      = squareFrac      + sqr_sig[1:]
#            squareFracMinus = squareFracMinus + sqr_sig_minus[1:]
#            squareFracPlus  = squareFracPlus  + sqr_sig_plus[1:]
    
    squareFrac      = [scale[1] * 2. * np.arctan(np.sin(2. * np.pi * P['F'] * P['T'][i]                + PHI) / delta) / np.pi for i in xrange(P['COUNTER'])]
    squareFracMinus = [scale[1] * 2. * np.arctan(np.sin(2. * np.pi * P['F'] * (P['T'][i] - P['TSTEP']) + PHI) / delta) / np.pi for i in xrange(P['COUNTER'])]
    squareFracPlus  = [scale[1] * 2. * np.arctan(np.sin(2. * np.pi * P['F'] * (P['T'][i] + P['TSTEP']) + PHI) / delta) / np.pi for i in xrange(P['COUNTER'])]
    
    # Triangle wave
    triangleFrac      = [scale[2] * (1. - 2. * np.arccos((1. - delta) * np.sin(2. * np.pi * P['F'] * P['T'][i]                + PHI)) / np.pi) for i in xrange(P['COUNTER'])]
    triangleFracMinus = [scale[2] * (1. - 2. * np.arccos((1. - delta) * np.sin(2. * np.pi * P['F'] * (P['T'][i] - P['TSTEP']) + PHI)) / np.pi) for i in xrange(P['COUNTER'])]
    triangleFracPlus  = [scale[2] * (1. - 2. * np.arccos((1. - delta) * np.sin(2. * np.pi * P['F'] * (P['T'][i] + P['TSTEP']) + PHI)) / np.pi) for i in xrange(P['COUNTER'])]
    
    # Saw-tooth Wave
    xt  = [0.25 * (2. * P['F'] * P['T'][i]                - 1.) for i in xrange(P['COUNTER'])]
    xtm = [0.25 * (2. * P['F'] * (P['T'][i] - P['TSTEP']) - 1.) for i in xrange(P['COUNTER'])]
    xtp = [0.25 * (2. * P['F'] * (P['T'][i] + P['TSTEP']) - 1.) for i in xrange(P['COUNTER'])]
    
    xs  = [0.5 * P['F'] * P['T'][i]                for i in xrange(P['COUNTER'])]
    xsm = [0.5 * P['F'] * (P['T'][i] - P['TSTEP']) for i in xrange(P['COUNTER'])]
    xsp = [0.5 * P['F'] * (P['T'][i] + P['TSTEP']) for i in xrange(P['COUNTER'])]
    
    trg  = [1. - 2. * np.arccos((1. - delta) * np.sin(2. * np.pi * xt[i] )) / np.pi for i in xrange(P['COUNTER'])]
    trgM = [1. - 2. * np.arccos((1. - delta) * np.sin(2. * np.pi * xtm[i])) / np.pi for i in xrange(P['COUNTER'])]
    trgP = [1. - 2. * np.arccos((1. - delta) * np.sin(2. * np.pi * xtp[i])) / np.pi for i in xrange(P['COUNTER'])]
    
    sqr  = [2. * np.arctan(np.sin(2. * np.pi * xs[i] ) / delta) / np.pi for i in xrange(P['COUNTER'])]
    sqrM = [2. * np.arctan(np.sin(2. * np.pi * xsm[i]) / delta) / np.pi for i in xrange(P['COUNTER'])]
    sqrP = [2. * np.arctan(np.sin(2. * np.pi * xsp[i]) / delta) / np.pi for i in xrange(P['COUNTER'])]
    
    sawFrac      = [scale[3] * trg[i]  * sqr[i]  for i in xrange(P['COUNTER'])]
    sawFracMinus = [scale[3] * trgM[i] * sqrM[i] for i in xrange(P['COUNTER'])]
    sawFracPlus  = [scale[3] * trgP[i] * sqrP[i] for i in xrange(P['COUNTER'])]

    # Form composite signal
    signal      = [P['SIG_WEIGHT'][0] * sineFrac[i]      + P['SIG_WEIGHT'][1] * squareFrac[i]      + P['SIG_WEIGHT'][2] * triangleFrac[i]      + P['SIG_WEIGHT'][3] * sawFrac[i]      for i in xrange(P['COUNTER'])]
    signalMinus = [P['SIG_WEIGHT'][0] * sineFracMinus[i] + P['SIG_WEIGHT'][1] * squareFracMinus[i] + P['SIG_WEIGHT'][2] * triangleFracMinus[i] + P['SIG_WEIGHT'][3] * sawFracMinus[i] for i in xrange(P['COUNTER'])]
    signalPlus  = [P['SIG_WEIGHT'][0] * sineFracPlus[i]  + P['SIG_WEIGHT'][1] * squareFracPlus[i]  + P['SIG_WEIGHT'][2] * triangleFracPlus[i]  + P['SIG_WEIGHT'][3] * sawFracPlus[i]  for i in xrange(P['COUNTER'])]
    
    return(signal, signalMinus, signalPlus)

def accel_multi_kinematics(P, PHI=0., scale=None, rate=50):
    delta = 1. / rate
    if (scale == None):
        x = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50]
        y = [2.1790849673, 1.5669799009, 1.3790259591, 1.2876797596, 1.2332922318, 1.1968681428, 1.1709601874, 1.1512761466, 1.136077986, 1.1238710886]
        scaleSig = PchipInterpolator(x, y)
        scale = [scaleSig(rate), scaleSig(rate), scaleSig(rate), scaleSig(rate)]
    
    # Sine Wave Input
    sineFrac      = [scale[0] * -4. * np.pi**2 * P['F']**2 * np.sin(2 * np.pi * P['F'] * P['T'][i] + PHI) for i in xrange(P['COUNTER'])]
    sineFracMinus = [scale[0] * -4. * np.pi**2 * P['F']**2 * np.sin(2 * np.pi * P['F'] * (P['T'][i] - P['TSTEP']) + PHI) for i in xrange(P['COUNTER'])]
    sineFracPlus  = [scale[0] * -4. * np.pi**2 * P['F']**2 * np.sin(2 * np.pi * P['F'] * (P['T'][i] + P['TSTEP']) + PHI) for i in xrange(P['COUNTER'])]
    
    # Square Wave Input  
    squareFrac      = [scale[1] * -16. * np.pi * delta * P['F']**2 * np.sin(2. * np.pi * P['F'] * P['T'][i]                + PHI) * (2. * delta**2 + np.cos(4. * np.pi * P['F'] * P['T'][i]                + 2. * PHI) + 3.) / (2. * delta**2 - np.cos(4. * np.pi * P['F'] * P['T'][i]                + 2. * PHI) + 1.)**2 for i in xrange(P['COUNTER'])]
    squareFracMinus = [scale[1] * -16. * np.pi * delta * P['F']**2 * np.sin(2. * np.pi * P['F'] * (P['T'][i] - P['TSTEP']) + PHI) * (2. * delta**2 + np.cos(4. * np.pi * P['F'] * (P['T'][i] - P['TSTEP']) + 2. * PHI) + 3.) / (2. * delta**2 - np.cos(4. * np.pi * P['F'] * (P['T'][i] - P['TSTEP']) + 2. * PHI) + 1.)**2 for i in xrange(P['COUNTER'])]
    squareFracPlus  = [scale[1] * -16. * np.pi * delta * P['F']**2 * np.sin(2. * np.pi * P['F'] * (P['T'][i] + P['TSTEP']) + PHI) * (2. * delta**2 + np.cos(4. * np.pi * P['F'] * (P['T'][i] + P['TSTEP']) + 2. * PHI) + 3.) / (2. * delta**2 - np.cos(4. * np.pi * P['F'] * (P['T'][i] + P['TSTEP']) + 2. * PHI) + 1.)**2 for i in xrange(P['COUNTER'])]
    
    # Triangle wave
    triangleFrac      = [scale[2] * -16. * np.sqrt(2.) * np.pi * (delta - 2.) * (delta - 1.) * delta * P['F']**2 * np.sin(2. * np.pi * P['F'] * P['T'][i]                + PHI) / (-delta**2 + 2. * delta + (delta - 1.)**2 * np.cos(2. * (2. * np.pi * P['F'] * P['T'][i]                + PHI)) + 1.)**1.5 for i in xrange(P['COUNTER'])]
    triangleFracMinus = [scale[2] * -16. * np.sqrt(2.) * np.pi * (delta - 2.) * (delta - 1.) * delta * P['F']**2 * np.sin(2. * np.pi * P['F'] * (P['T'][i] - P['TSTEP']) + PHI) / (-delta**2 + 2. * delta + (delta - 1.)**2 * np.cos(2. * (2. * np.pi * P['F'] * (P['T'][i] - P['TSTEP']) + PHI)) + 1.)**1.5 for i in xrange(P['COUNTER'])]
    triangleFracPlus  = [scale[2] * -16. * np.sqrt(2.) * np.pi * (delta - 2.) * (delta - 1.) * delta * P['F']**2 * np.sin(2. * np.pi * P['F'] * (P['T'][i] + P['TSTEP']) + PHI) / (-delta**2 + 2. * delta + (delta - 1.)**2 * np.cos(2. * (2. * np.pi * P['F'] * (P['T'][i] + P['TSTEP']) + PHI)) + 1.)**1.5 for i in xrange(P['COUNTER'])]
    
    # Saw-tooth Wave
    xt  = [0.25 * (2. * P['F'] * P['T'][i]                - 1.) for i in xrange(P['COUNTER'])]
    xtm = [0.25 * (2. * P['F'] * (P['T'][i] - P['TSTEP']) - 1.) for i in xrange(P['COUNTER'])]
    xtp = [0.25 * (2. * P['F'] * (P['T'][i] + P['TSTEP']) - 1.) for i in xrange(P['COUNTER'])]
    
    xs  = [0.5 * P['F'] * P['T'][i]                for i in xrange(P['COUNTER'])]
    xsm = [0.5 * P['F'] * (P['T'][i] - P['TSTEP']) for i in xrange(P['COUNTER'])]
    xsp = [0.5 * P['F'] * (P['T'][i] + P['TSTEP']) for i in xrange(P['COUNTER'])]
    
    trg  = [-16. * np.sqrt(2.) * np.pi * (delta - 2.) * (delta - 1.) * delta * np.sin(2. * np.pi * xt[i] ) / (-delta**2 + 2. * delta + (delta - 1.)**2 * np.cos(2. * (2. * np.pi * xt[i] )) + 1.)**1.5 for i in xrange(P['COUNTER'])]
    trgM = [-16. * np.sqrt(2.) * np.pi * (delta - 2.) * (delta - 1.) * delta * np.sin(2. * np.pi * xtm[i]) / (-delta**2 + 2. * delta + (delta - 1.)**2 * np.cos(2. * (2. * np.pi * xtm[i])) + 1.)**1.5 for i in xrange(P['COUNTER'])]
    trgP = [-16. * np.sqrt(2.) * np.pi * (delta - 2.) * (delta - 1.) * delta * np.sin(2. * np.pi * xtp[i]) / (-delta**2 + 2. * delta + (delta - 1.)**2 * np.cos(2. * (2. * np.pi * xtp[i])) + 1.)**1.5 for i in xrange(P['COUNTER'])]
    
    sqr  = [-16. * np.pi * delta * np.sin(2. * np.pi * xs[i]) * (2. * delta**2 + np.cos(4. * np.pi * xs[i] ) + 3.) / (2. * delta**2 - np.cos(4. * np.pi * xs[i] ) + 1.)**2 for i in xrange(P['COUNTER'])]
    sqrM = [-16. * np.pi * delta * np.sin(2. * np.pi * xs[i]) * (2. * delta**2 + np.cos(4. * np.pi * xsm[i]) + 3.) / (2. * delta**2 - np.cos(4. * np.pi * xsm[i]) + 1.)**2 for i in xrange(P['COUNTER'])]
    sqrP = [-16. * np.pi * delta * np.sin(2. * np.pi * xs[i]) * (2. * delta**2 + np.cos(4. * np.pi * xsp[i]) + 3.) / (2. * delta**2 - np.cos(4. * np.pi * xsp[i]) + 1.)**2 for i in xrange(P['COUNTER'])]
    
    sawFrac      = [scale[3] * trg[i]  * sqr[i]  for i in xrange(P['COUNTER'])]
    sawFracMinus = [scale[3] * trgM[i] * sqrM[i] for i in xrange(P['COUNTER'])]
    sawFracPlus  = [scale[3] * trgP[i] * sqrP[i] for i in xrange(P['COUNTER'])]

    # Form composite signal
    signal      = [P['SIG_WEIGHT'][0] * sineFrac[i]      + P['SIG_WEIGHT'][1] * squareFrac[i]      + P['SIG_WEIGHT'][2] * triangleFrac[i]      + P['SIG_WEIGHT'][3] * sawFrac[i]      for i in xrange(P['COUNTER'])]
    signalMinus = [P['SIG_WEIGHT'][0] * sineFracMinus[i] + P['SIG_WEIGHT'][1] * squareFracMinus[i] + P['SIG_WEIGHT'][2] * triangleFracMinus[i] + P['SIG_WEIGHT'][3] * sawFracMinus[i] for i in xrange(P['COUNTER'])]
    signalPlus  = [P['SIG_WEIGHT'][0] * sineFracPlus[i]  + P['SIG_WEIGHT'][1] * squareFracPlus[i]  + P['SIG_WEIGHT'][2] * triangleFracPlus[i]  + P['SIG_WEIGHT'][3] * sawFracPlus[i]  for i in xrange(P['COUNTER'])]
    
    return(signal, signalMinus, signalPlus)
