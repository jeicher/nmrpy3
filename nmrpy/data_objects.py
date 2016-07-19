import numpy
import scipy
import pylab
import lmfit
import nmrglue
import numbers
from scipy.optimize import leastsq
from multiprocessing import Pool, cpu_count
from nmrpy.plotting import *
import pickle

class Base():
    _complex_dtypes = [
                    numpy.dtype('complex64'),
                    numpy.dtype('complex128'),
                    numpy.dtype('complex256'),
                    ]

    _file_formats = ['varian', 'bruker', None]

    def __init__(self, *args, **kwargs):
        self.id = kwargs.get('id', None)
        self._procpar = kwargs.get('procpar', None)
        self._params = None
        self.fid_path = kwargs.get('fid_path', '.')
        self._file_format = None

    @property
    def id(self):
        return self.__id

    @id.setter
    def id(self, id):
        if isinstance(id, str) or id is None:
            self.__id = id
        else:
            raise AttributeError('ID must be a string or None.')
        
    @property
    def fid_path(self):
        return self.__fid_path

    @fid_path.setter
    def fid_path(self, fid_path):
        if isinstance(fid_path, str):
            self.__fid_path = fid_path
        else:
            raise AttributeError('fid_path must be a string.')

    @property
    def _file_format(self):
        return self.__file_format

    @_file_format.setter
    def _file_format(self, file_format):
        if file_format in self._file_formats:
            self.__file_format = file_format
        else:
            raise AttributeError('_file_format must be "varian", "bruker", or None.')

    @classmethod
    def _is_iter(cls, i):
        try:
            iter(i)
            return True
        except TypeError:
            return False

    @classmethod
    def _is_iter_of_iters(cls, i):
        if i == []:
            return False
        elif cls._is_iter(i) and all(cls._is_iter(j) for j in i):
            return True
        return False

    @classmethod
    def _is_flat_iter(cls, i):
        if i == []:
            return True
        elif cls._is_iter(i) and not any(cls._is_iter(j) for j in i):
            return True
        return False

    @property
    def _procpar(self):
        return self.__procpar

    @_procpar.setter
    def _procpar(self, procpar):
        if procpar is None:
            self.__procpar = procpar 
        elif isinstance(procpar, dict):
            self.__procpar = procpar 
            self._params = self._extract_procpar(procpar)
        else:
            raise AttributeError('procpar must be a dictionary or None.')

    @property
    def _params(self):
        return self.__params

    @_params.setter
    def _params(self, params):
        if isinstance(params, dict) or params is None:
            self.__params = params
        else:
            raise AttributeError('params must be a dictionary or None.')

    #processing
    def _extract_procpar(self, procpar):
        if self._file_format == 'bruker':
            return self._extract_procpar_bruker(procpar, self.fid_path)
        elif self._file_format == 'varian':
            return self._extract_procpar_varian(procpar)
        #else:
        #    raise AttributeError('Could not parse procpar.') 

    @staticmethod
    def _extract_procpar_varian(procpar):
        """
        Extract some commonely-used NMR parameters (using Varian denotations)
        and return a parameter dictionary 'params'.
        """
        at = float(procpar['procpar']['at']['values'][0])
        d1 = float(procpar['procpar']['d1']['values'][0])
        sfrq = float(procpar['procpar']['sfrq']['values'][0])
        reffrq = float(procpar['procpar']['reffrq']['values'][0])
        rfp = float(procpar['procpar']['rfp']['values'][0])
        rfl = float(procpar['procpar']['rfl']['values'][0])
        tof = float(procpar['procpar']['tof']['values'][0])
        rt = at+d1
        nt = numpy.array(
            [procpar['procpar']['nt']['values']], dtype=float)
        acqtime = (nt*rt).cumsum()/60.  # convert to mins.
        sw = round(
            float(procpar['procpar']['sw']['values'][0]) /
            float(procpar['procpar']['sfrq']['values'][0]), 2)
        sw_hz = float(procpar['procpar']['sw']['values'][0])
        sw_left = (0.5+1e6*(sfrq-reffrq)/sw_hz)*sw_hz/sfrq
        params = dict(
            at=at,
            d1=d1,
            rt=rt,
            nt=nt,
            acqtime=acqtime,
            sw=sw,
            sw_hz=sw_hz,
            sfrq=sfrq,
            reffrq=reffrq,
            rfp=rfp,
            rfl=rfl,
            tof=tof,
            sw_left=sw_left,
            )
        return params

    @staticmethod
    def _extract_procpar_bruker(procpar, filepath): #finish this
        """
        Extract some commonly-used NMR parameters (using Bruker denotations)
        and return a parameter dictionary 'params'.
        """
        d1 = procpar['RD']
        reffrq = procpar['SFO1']
        nt = procpar['NS']
        sw_hz = procpar['SW_h']
        sw = procpar['SW']
        # lefthand offset of the processed data in ppm
        for i in open(filepath+'/pdata/1/procs').readlines():
            if 'OFFSET' in i:
                sw_left = float(i.split(' ')[1])
        at = procpar['TD']/(2*sw_hz)
        rt = at+d1
        acqtime = (nt*rt)/60.  # convert to mins.
        params = dict(
            at=at,
            d1=d1,
            reffrq=reffrq,
            rt=rt,
            nt=nt,
            acqtime=acqtime,
            sw_left=sw_left,
            sw=sw,
            sw_hz=sw_hz)
        return params

class Fid(Base):
    '''
    The basic FID (Free Induction Decay) class contains all the data for a single spectrum (:attr:`~nmrpy.data_objects.Fid.data`), and the
    necessary methods to process these data.
    '''    

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.data = kwargs.get('data', [])
        self.peaks = None
        self.ranges = None
        self._deconvoluted_peaks = None
        self._flags = {
            "ft": False,
        }


    def __str__(self):
        return 'FID: %s (%i data)'%(self.id, len(self.data))

    @property
    def data(self):
        """
        The spectral data. This is the primary object upon which the processing and analysis functions work.
        """
        return self.__data
    
    @data.setter    
    def data(self, data):
        if Fid._is_valid_dataset(data):
            self.__data = numpy.array(data)

    @property
    def _ppm(self):
        """
        Index of :attr:`~nmrpy.data_objects.Fid.data` in ppm (parts per million).
        """
        if self._params is not None and self.data is not None:
            return numpy.linspace(self._params['sw_left']-self._params['sw'], self._params['sw_left'], len(self.data))[::-1]
        else:
            return None

    @property
    def peaks(self):
        """
        Picked peaks for deconvolution of :attr:`~nmrpy.data_objects.Fid.data`.
        """
        return self._peaks
    
    @peaks.setter    
    def peaks(self, peaks):
        if peaks is not None:
            if not Fid._is_flat_iter(peaks):
                raise AttributeError('peaks must be a flat iterable')
            if not all(isinstance(i, numbers.Number) for i in peaks):
                raise AttributeError('peaks must be numbers')
            self._peaks = numpy.array(peaks)
        else:
            self._peaks = peaks

    @property
    def ranges(self):
        """
        Picked ranges for deconvolution of :attr:`~nmrpy.data_objects.Fid.data`.
        """
        return self._ranges
    
    @ranges.setter    
    def ranges(self, ranges):
        if ranges == None:
            self._ranges = None
            return
        if not Fid._is_iter_of_iters(ranges) or ranges is None:
            raise AttributeError('ranges must be an iterable of iterables or None')
        ranges = numpy.array(ranges)
        if ranges.shape[1] != 2:
            raise AttributeError('ranges must be an iterable of 2-length iterables or an empty iterables e.g. [[]]')
        for r in ranges:
            if not all(isinstance(i, numbers.Number) for i in r):
                raise AttributeError('ranges must be numbers')
        self._ranges = ranges

    @property
    def _bl_ppm(self):
        return self.__bl_ppm
    
    @_bl_ppm.setter    
    def _bl_ppm(self, bl_ppm):
        if bl_ppm is not None:
            if not Fid._is_flat_iter(bl_ppm):
                raise AttributeError('baseline indices must be a flat iterable')
            if len(bl_ppm) > 0:
                if not all(isinstance(i, numbers.Number) for i in bl_ppm):
                    raise AttributeError('baseline indices must be numbers')
                self.__bl_ppm = numpy.sort(list(set(bl_ppm)))[::-1]
            else:
                self.__bl_ppm = None
        else:
            self.__bl_ppm = bl_ppm

    @property
    def _bl_indices(self):
        if self._bl_ppm is not None:
            return self._conv_to_index(self.data, self._bl_ppm, self._params['sw_left'], self._params['sw'])
        else:
            return None

    @property
    def _bl_poly(self):
        return self.__bl_poly
    
    @_bl_poly.setter    
    def _bl_poly(self, bl_poly):
        if bl_poly is not None:
            if not Fid._is_flat_iter(bl_poly):
                raise AttributeError('baseline polynomial must be a flat iterable')
            if not all(isinstance(i, numbers.Number) for i in bl_poly):
                raise AttributeError('baseline polynomial must be numbers')
            self.__bl_poly = numpy.array(bl_poly)
        else:
            self.__bl_ppm = bl_poly

    @property
    def _index_peaks(self):
        """
        :attr:`~nmrpy.data_objects.Fid.peaks` converted to indices rather than ppm
        """
        if self.peaks is not None:
            return self._conv_to_index(self.data, self.peaks, self._params['sw_left'], self._params['sw'])
        else:
            return [] 

    @property
    def _index_ranges(self):
        """
        :attr:`~nmrpy.data_objects.Fid.ranges` converted to indices rather than ppm
        """
        if self.ranges is not None:
            shp = self.ranges.shape
            index_ranges = self._conv_to_index(self.data, self.ranges.flatten(), self._params['sw_left'], self._params['sw'])
            return index_ranges.reshape(shp)
        else:
            return [] 

    @property
    def _grouped_peaklist(self):
        """
        :attr:`~nmrpy.data_objects.Fid.peaks` grouped according to :attr:`~nmrpy.data_objects.Fid.ranges`
        """
        if self.ranges is not None:
            return numpy.array([[peak for peak in self.peaks if peak > min(peak_range) and peak < max(peak_range)]
                    for peak_range in self.ranges])
        else:
            return []
    @property
    def _grouped_index_peaklist(self):
        """
        :attr:`~nmrpy.data_objects.Fid._index_peaks` grouped according to :attr:`~nmrpy.data_objects.Fid._index_ranges`
        """
        if self._index_ranges is not None:
            return numpy.array([[peak for peak in self._index_peaks if peak > min(peak_range) and peak < max(peak_range)]
                    for peak_range in self._index_ranges])
        else:
            return []

    @property
    def _deconvoluted_peaks(self):
        return self.__deconvoluted_peaks

    @_deconvoluted_peaks.setter
    def _deconvoluted_peaks(self, deconvoluted_peaks):
        """This is a list of lists of peak parameters with the order [offset, gauss_sigma, lorentz_hwhm, amplitude, frac_gauss]:

            offset: spectral offset

            gauss_sigma: Gaussian sigma

            lorentz_hwhm: Lorentzian half-width-at-half-maximum

            amplitude: height of peak

            frac_gauss: fraction of peak to be Gaussian (Lorentzian fraction is 1-frac_gauss)
         """
        self.__deconvoluted_peaks = deconvoluted_peaks 

    @property
    def deconvoluted_integrals(self):
        """
        An array of integrals for each deconvoluted peak.
        """
        if self._deconvoluted_peaks is not None:
            integrals = []
            for peak in self._deconvoluted_peaks:
                int_gauss = peak[-1]*Fid._f_gauss_int(peak[3], peak[1])
                int_lorentz = (1-peak[-1])*Fid._f_lorentz_int(peak[3], peak[2])
                integrals.append(int_gauss+int_lorentz)
            return numpy.array(integrals)
            
    def _get_plots(self):
        """
        Return a list of all :class:`~nmrpy.plotting.Plot` objects owned by this :class:`~nmrpy.data_objects.Fid`.
        """
        plots = [self.__dict__[id] for id in sorted(self.__dict__) if isinstance(self.__dict__[id], Plot)]
        return plots

    def _del_plots(self):
        """
        Deletes all :class:`~nmrpy.plotting.Plot` objects owned by this :class:`~nmrpy.data_objects.Fid`.
        """
        plots = self._get_plots()
        for plot in plots:
            delattr(self, plot.id)


    @classmethod
    def _is_valid_dataset(cls, data):
        if isinstance(data, str):
            raise TypeError('Data must be iterable not a string.')
        if not cls._is_iter(data):
            raise TypeError('Data must be an iterable.')
        if not cls._is_flat_iter(data):
            raise TypeError('Data must not be nested.')
        if not all(isinstance(i, numbers.Number) for i in data):
            raise TypeError('Data must consist of numbers only.')
        return True 
        

    @classmethod
    def from_data(cls, data):
        """

        Instantiate a new :class:`~nmrpy.data_objects.Fid` object by providing a
        spectral data object as argument. Eg. ::

            fid = Fid.from_data(data) 
        """
        new_instance = cls()
        new_instance.data = data
        return new_instance  

    def zf(self):
        """

        Apply a single degree of zero-filling to data array
        :attr:`~nmrpy.data_objects.Fid.data`.

        Note: extends data to double length by appending zeroes. This results
        in an artificially increased resolution once Fourier-transformed.

        """
        self.data = numpy.append(self.data, 0*self.data)

    def emhz(self, lb=5.0):
        """

        Apply exponential line-broadening to data array
        :attr:`~nmrpy.data_objects.Fid.data`.

        :keyword lb: degree of line-broadening in Hz.

        """
        self.data = numpy.exp(-numpy.pi*numpy.arange(len(self.data)) * (lb/self._params['sw_hz'])) * self.data

    def real(self):
        """
        Discard imaginary component of :attr:`~nmrpy.data_objects.Fid.data`.
        """
        self.data = numpy.real(self.data)

    # GENERAL FUNCTIONS
    def ft(self):
        """
        Fourier Transform the data array :attr:`~nmrpy.data_objects.Fid.data`.

        Calculates the Discrete Fourier Transform using the Fast Fourier
        Transform algorithm as implemented in NumPy (*Cooley, James W., and John W.
        Tukey, 1965, 'An algorithm for the machine calculation of complex Fourier
        series,' Math. Comput. 19: 297-301.*)

        """
        if self._flags['ft']:
                raise ValueError('Data have already been Fourier Transformed.')
        if Fid._is_valid_dataset(self.data):
            list_params = (self.data, self._file_format)
            self.data = Fid._ft(list_params)
            self._flags['ft'] = True

    @classmethod
    def _ft(cls, list_params):
        """
        Class method for Fourier-transforming data using multiprocessing.
        list_params is a tuple of (<data>, <file_format>).
        """
        if len(list_params) != 2:
            raise ValueError('Wrong number of parameters. list_params must contain [<data>, <file_format>]')
        data, file_format = list_params
        if Fid._is_valid_dataset(data) and file_format in Fid._file_formats:
            data = numpy.array(numpy.fft.fft(data), dtype=data.dtype)
            s = len(data)
            if file_format == 'varian' or file_format == None:
                    ft_data = numpy.append(data[int(s / 2.0):], data[: int(s / 2.0)])
            if file_format == 'bruker':
                    ft_data = numpy.append(data[int(s / 2.0):: -1], data[s: int(s / 2.0): -1])
            return ft_data


    @staticmethod
    def _conv_to_ppm(data, index, sw_left, sw):
            """
            Convert index array to ppm. 
            """
            if isinstance(index, list):
                    index = numpy.array(index)
            frc_sw = index/float(len(data))
            ppm = sw_left-sw*frc_sw
            if Fid._is_iter(ppm):
                return numpy.array([round(i, 2) for i in ppm])
            else:
                return round(ppm, 2)

    @staticmethod
    def _conv_to_index(data, ppm, sw_left, sw):
            """
            Convert ppm array to index. 
            """
            conv_to_int = False
            if not Fid._is_iter(ppm):
                ppm = [ppm]
                conv_to_int = True
            if isinstance(ppm, list):
                    ppm = numpy.array(ppm)
            if any(ppm > sw_left) or any(ppm < sw_left-sw):
                raise ValueError('ppm must be within spectral width.')
            indices = len(data)*(sw_left-ppm)/sw
            if conv_to_int:
                return int(numpy.ceil(indices))
            return numpy.array(numpy.ceil(indices), dtype=int)
    
    def phase_correct(self, method='leastsq'):
            """

            Automatically phase-correct :attr:`~nmrpy.data_objects.Fid.data` by minimising
            total absolute area.

            :keyword method: The fitting method to use. Default is 'leastsq', the Levenberg-Marquardt algorithm, which is usually sufficient. Additional options include:
                    
                    Nelder-Mead (nelder)

                    L-BFGS-B (l-bfgs-b)

                    Conjugate Gradient (cg)

                    Powell (powell)

                    Newton-CG  (newton)
            """
            if self.data.dtype not in self._complex_dtypes:
                raise TypeError('Only complex data can be phase-corrected.')
            if not self._flags['ft']:
                raise ValueError('Only Fourier-transformed data can be phase-corrected.')
            print('phasing: %s'%self.id)
            self.data = Fid._phase_correct((self.data, method))

    @classmethod
    def _phase_correct(cls, list_params):
            """
            Class method for phase-correction using multiprocessing.
            list_params is a tuple of (<data>, <fitting method>).
            """
            data, method = list_params
            p = lmfit.Parameters()
            p.add_many(
                    ('p0', 1.0, True),
                    ('p1', 0.0, True),
                    )
            mz = lmfit.minimize(Fid._phased_data_sum, p, args=([data]), method=method)
            phased_data = Fid._ps(data, p0=mz.params['p0'].value, p1=mz.params['p1'].value)
            if abs(phased_data.min()) > abs(phased_data.max()):
                    phased_data *= -1
            print('%d\t%d'%(mz.params['p0'].value, mz.params['p1'].value))
            return phased_data
        
    @classmethod
    def _phased_data_sum(cls, pars, data):
            err = Fid._ps(data, p0=pars['p0'].value, p1=pars['p1'].value).real
            return numpy.array([abs(err).sum()]*2)

    @classmethod
    def _ps(cls, data, p0=0.0, p1=0.0):
            """
            Linear phase correction
            
            :keyword p0: Zero order phase in degrees.
    
            :keyword p1: First order phase in degrees.

            """
            if not all(isinstance(i, (float, int)) for i in [p0, p1]):
                raise TypeError('p0 and p1 must be floats or ints.')
            if not data.dtype in Fid._complex_dtypes:
                raise TypeError('data must be complex.')
            # convert to radians
            p0 = p0*numpy.pi/180.0
            p1 = p1*numpy.pi/180.0
            size = len(data)
            ph = numpy.exp(1.0j*(p0+(p1*numpy.arange(size)/size)))
            return ph*data

    def ps(self, p0=0.0, p1=0.0):
        """
        Linear phase correction of :attr:`~nmrpy.data_objects.Fid.data`
        
        :keyword p0: Zero order phase in degrees

        :keyword p1: First order phase in degrees
        
        """
        if not all(isinstance(i, (float, int)) for i in [p0, p1]):
            raise TypeError('p0 and p1 must be floats or ints.')
        if not self.data.dtype in self._complex_dtypes:
            raise TypeError('data must be complex.')
        # convert to radians
        p0 = p0*numpy.pi/180.0
        p1 = p1*numpy.pi/180.0
        size = len(self.data)
        ph = numpy.exp(1.0j*(p0+(p1*numpy.arange(size)/size)))
        self.data = ph*self.data

    def phaser(self):
        """
        Instantiate a phase-correction GUI widget which applies to :attr:`~nmrpy.data_objects.Fid.data`.
        """
        if not len(self.data):
            raise AttributeError('data does not exist.')
        if self.data.dtype not in self._complex_dtypes:
            raise TypeError('data must be complex.')
        if not Fid._is_flat_iter(self.data):
            raise AttributeError('data must be 1 dimensional.')
        global _phaser_widget
        _phaser_widget = Phaser(self)

    def baseline_correct(self, deg=2):
        """

        Perform baseline correction by fitting specified baseline points
        (stored in :attr:`~nmrpy.data_objects.Fid._bl_ppm`) with polynomial of specified
        degree (stored in :attr:`~nmrpy.data_objects.Fid._bl_ppm`) and subtract this
        polynomial from :attr:`~nmrpy.data_objects.Fid.data`.
        

        :keyword deg: degree of fitted polynomial
        """

        if self._bl_indices is None:
            raise AttributeError('No points selected for baseline correction. Run fid.baseliner()')
        if not len(self.data):
            raise AttributeError('data does not exist.')
        if self.data.dtype in self._complex_dtypes:
            raise TypeError('data must be not be complex.')
        if not Fid._is_flat_iter(self.data):
            raise AttributeError('data must be 1 dimensional.')
        
        data = self.data
        x = numpy.arange(len(data))
        m = numpy.ones_like(x)
        m[self._bl_indices] = 0
        self._bl_poly = []
        ym = numpy.ma.masked_array(data, m)
        xm = numpy.ma.masked_array(x, m)
        p = numpy.ma.polyfit(xm, ym, deg)
        yp = scipy.polyval(p, x)
        self._bl_poly = yp
        data_bl = data-yp
        self.data = numpy.array(data_bl)

    def peakpick(self, thresh=0.1):
        """ 

        Attempt to automatically identify peaks. Picked peaks are assigned to
        :attr:`~nmrpy.data_objects.Fid.peaks`.

        :keyword thresh: fractional threshold for peak-picking
        """
        peaks_ind = nmrglue.peakpick.pick(self.data, thresh*self.data.max())
        peaks_ind = [i[0] for i in peaks_ind]
        peaks_ppm = Fid._conv_to_ppm(self.data, peaks_ind, self._params['sw_left'], self._params['sw'])
        self.peaks = peaks_ppm
        print(self.peaks)

    def peakpicker(self):
        """

        Instantiate a peak-picking GUI widget. Left-clicking selects a peak.
        Right-click-dragging defines a range. Middle-clicking deletes nearest peak;
        ctrl-middle click deletes range. Peaks are stored in
        :attr:`~nmrpy.data_objects.Fid.peaks`; ranges are stored in
        :attr:`~nmrpy.data_objects.Fid.ranges`: both are used for deconvolution (see
        :meth:`~nmrpy.data_objects.Fid.deconv`).

        """
        global _peakpicker_widget
        plot_label = 'Left - select peak\nMiddle - delete nearest peak\nCtrl/Middle - delete range\nDrag Right - select range'
        _peakpicker_widget = DataSelector(self.data, self._params, 
                            peaks=self.peaks,
                            ranges=self.ranges,
                            title="Peak-picking {}".format(self.id), 
                            label=plot_label)
        if len(_peakpicker_widget.ranges) > 0 and len(_peakpicker_widget.peaks) > 0:
            self.ranges = _peakpicker_widget.ranges
            peaks = []
            for peak in _peakpicker_widget.peaks:
                for rng in self.ranges:
                    if peak >= rng[1] and peak <= rng[0]:
                        peaks.append(peak)
            self.peaks = peaks
        else:
            self.peaks = None
            self.ranges = None

    def baseliner(self):
        """
        Instantiate a baseline-correction GUI widget. Left-clicking selects an
        index. Right-click-dragging defines a range. Middle-clicking deletes previously
        selected peak. Indices selected either as individual selections (left-click) or
        in ranges (right-click) are combined and stored in
        :attr:`~nmrpy.data_objects.Fid._bl_ppm`, which is used for baseline-correction
        (see :meth:`~nmrpy.data_objects.Fid.baseline_correction`).

        """
        global _baseliner_widget
        plot_label = 'Left - select datum\nMiddle - delete last selection\nDrag Right - select range'
        plot_title = 'Select data for baseline-correction'
        _baseliner_widget = DataSelector(self.data, self._params, title=plot_title, label=plot_label)
        bl_ppm = []
        for rng in _baseliner_widget.ranges:
            peak_ind = (self._ppm > rng[1]) * (self._ppm < rng[0])
            cur_peaks = self._ppm[peak_ind]
            bl_ppm.append(cur_peaks)
        if len(_baseliner_widget.peaks) > 0:
            bl_ppm.append(_baseliner_widget.peaks)
        bl_ppm = numpy.array([j for i in bl_ppm for j in i])
        self._bl_ppm = bl_ppm
  

    @classmethod
    def _f_gauss(cls, offset, amplitude, gauss_sigma, x):
        return amplitude*numpy.exp(-((offset-x)**2.0)/(2.0*gauss_sigma**2.0))
    
    @classmethod
    def _f_lorentz(cls, offset, amplitude, lorentz_hwhm, x):
        #return amplitude*lorentz_hwhm**2.0/(lorentz_hwhm**2.0+4.0*(offset-x)**2.0)
        return amplitude*lorentz_hwhm**2.0/(lorentz_hwhm**2.0+(x-offset)**2.0)

    @classmethod
    def _f_gauss_int(cls, amplitude, gauss_sigma):
        return amplitude*numpy.sqrt(2.0*numpy.pi*gauss_sigma**2.0)

    @classmethod
    def _f_lorentz_int(cls, amplitude, lorentz_hwhm):
        #empirical integral commented out
        #x = numpy.arange(1000*lorentz_hwhm)
        #return numpy.sum(amplitude*lorentz_hwhm**2.0/(lorentz_hwhm**2.0+(x-len(x)/2)**2.0))
        #this integral forumula from http://magicplot.com/wiki/fit_equations
        return amplitude*lorentz_hwhm*numpy.pi

    @classmethod
    def _f_pk(cls, x, offset=0.0, gauss_sigma=1.0, lorentz_hwhm=1.0, amplitude=1.0, frac_gauss=0.0):
        """

        Return the a combined Gaussian/Lorentzian peakshape for deconvolution
        of :attr:`~nmrpy.data_objects.Fid.data`.
        
        :arg x: array of equal length to :attr:`~nmrpy.data_objects.Fid.data`
        

        :keyword offset: spectral offset in x

        :keyword gauss_sigma: 2*sigma**2 specifying the width of the Gaussian peakshape

        :keyword lorentz_hwhm: Lorentzian half width at half maximum height

        :keyword amplitude: amplitude of peak

        :keyword frac_gauss: fraction of function to be Gaussian (0 -> 1). Note:
            specifying a Gaussian fraction of 0 will produce a pure Lorentzian and vice
            versa.  """
        
        #validation
        parameters = [offset, gauss_sigma, lorentz_hwhm, amplitude, frac_gauss]
        if not all(isinstance(i, numbers.Number) for i in parameters):
            raise TypeError('Keyword parameters must be numbers.') 
        if not cls._is_iter(x):
            raise TypeError('x must be an iterable') 
        if not isinstance(x, numpy.ndarray):
            x = numpy.array(x) 
        if frac_gauss > 1.0:
            frac_gauss = 1.0
        if frac_gauss < 0.0:
            frac_gauss = 0.0
        
        gauss_peak = cls._f_gauss(offset, amplitude, gauss_sigma, x)
        lorentz_peak = cls._f_lorentz(offset, amplitude, lorentz_hwhm, x)
        peak = frac_gauss*gauss_peak + (1-frac_gauss)*lorentz_peak
        
        return peak
   


    @classmethod
    def _f_makep(cls, data, peaks, frac_gauss=None):
        """
        Make a set of initial peak parameters for deconvolution.
        

        :arg data: data to be fitted

        :arg peaks: selected peak positions (see peakpicker())
       
        :returns: an array of peaks, each consisting of the following parameters:

                    spectral offset (x)

                    gauss: 2*sigma**2

                    lorentz: scale (HWHM)

                    amplitude: amplitude of peak

                    frac_gauss: fraction of function to be Gaussian (0 -> 1)
        """
        if not cls._is_flat_iter(data):
            raise TypeError('data must be a flat iterable') 
        if not cls._is_flat_iter(peaks):
            raise TypeError('peaks must be a flat iterable') 
        if not isinstance(data, numpy.ndarray):
            data = numpy.array(data) 
        
        p = []
        for i in peaks:
            pamp = 0.9*abs(data[i])
            single_peak = [i, 10, 0.1, pamp, frac_gauss]
            p.append(single_peak)
        return numpy.array(p)

    @classmethod
    def _f_conv(cls, parameterset_list, data):
        """
        Returns the maximum of a convolution of an initial set of lineshapes and the data to be fitted.
        
        parameterset_list -- a list of parameter lists: n*[[spectral offset (x), 
                                        gauss: 2*sigma**2, 
                                        lorentz: scale (HWHM), 
                                        amplitude: amplitude of peak, 
                                        frac_gauss: fraction of function to be Gaussian (0 -> 1)]]
                            where n is the number of peaks
        data -- 1D spectral array
        
        """

        if not cls._is_flat_iter(data):
            raise TypeError('data must be a flat iterable') 
        if not cls._is_iter(parameterset_list):
            raise TypeError('parameterset_list must be an iterable') 
        if not isinstance(data, numpy.ndarray):
            data = numpy.array(data) 
        
        data[data == 0.0] = 1e-6
        x = numpy.arange(len(data), dtype='f8')
        peaks_init = cls._f_pks(parameterset_list, x)
        data_convolution = numpy.convolve(data, peaks_init[::-1])
        auto_convolution = numpy.convolve(peaks_init, peaks_init[::-1])
        max_data_convolution = numpy.where(data_convolution == data_convolution.max())[0][0]
        max_auto_convolution = numpy.where(auto_convolution == auto_convolution.max())[0][0]
        return max_data_convolution - max_auto_convolution

    @classmethod 
    def _f_pks_list(cls, parameterset_list, x):
        """
        Return a list of peak evaluations for deconvolution. See _f_pk().
        
        Keyword arguments:
        parameterset_list -- a list of parameter lists: [spectral offset (x), 
                                        gauss: 2*sigma**2, 
                                        lorentz: scale (HWHM), 
                                        amplitude: amplitude of peak, 
                                        frac_gauss: fraction of function to be Gaussian (0 -> 1)]
        x -- array of equal length to FID
        """
        if not cls._is_iter_of_iters(parameterset_list):
            raise TypeError('Parameter set must be an iterable of iterables') 
        for p in parameterset_list:
            if not cls._is_iter(p):
                raise TypeError('Parameter set must be an iterable') 
            if not all(isinstance(i, numbers.Number) for i in p):
                raise TypeError('Keyword parameters must be numbers.') 
        if not cls._is_iter(x):
            raise TypeError('x must be an iterable') 
        if not isinstance(x, numpy.ndarray):
            x = numpy.array(x) 
        return numpy.array([Fid._f_pk(x, *peak) for peak in parameterset_list])
        

    @classmethod 
    def _f_pks(cls, parameterset_list, x):
        """
        Return the sum of a series of peak evaluations for deconvolution. See _f_pk().
        
        Keyword arguments:
        parameterset_list -- a list of parameter lists: [spectral offset (x), 
                                        gauss: 2*sigma**2, 
                                        lorentz: scale (HWHM), 
                                        amplitude: amplitude of peak, 
                                        frac_gauss: fraction of function to be Gaussian (0 -> 1)]
        x -- array of equal length to FID
        """
        
        if not cls._is_iter_of_iters(parameterset_list):
            raise TypeError('Parameter set must be an iterable of iterables') 
        for p in parameterset_list:
            if not cls._is_iter(p):
                raise TypeError('Parameter set must be an iterable') 
            if not all(isinstance(i, numbers.Number) for i in p):
                raise TypeError('Keyword parameters must be numbers.') 
        if not cls._is_iter(x):
            raise TypeError('x must be an iterable') 
        if not isinstance(x, numpy.ndarray):
            x = numpy.array(x) 
       
        peaks = x*0.0
        for p in parameterset_list:
            peak = cls._f_pk(x, 
                    offset=p[0], 
                    gauss_sigma=p[1], 
                    lorentz_hwhm=p[2], 
                    amplitude=p[3], 
                    frac_gauss=p[4],
                    )
            peaks += peak
        return peaks

    @classmethod
    def _f_res(cls, p, data):
        """
        Objective function for deconvolution. Returns residuals of the devonvolution fit.
        
        x -- array of equal length to FID
        
        Keyword arguments:
        p -- lmfit parameters object:
                            offset_n -- spectral offset in x
                            sigma_n -- gaussian 2*sigma**2
                            hwhm_n -- lorentzian half width at half maximum height
                            amplitude_n -- amplitude of peak
                            frac_gauss_n -- fraction of function to be Gaussian (0 -> 1)
            where n is the peak number (zero-indexed)
        data -- spectrum array
        
        """
        if not isinstance(p, lmfit.parameter.Parameters):
            raise TypeError('Parameters must be of type lmfit.parameter.Parameters.') 
        if not cls._is_flat_iter(data):
            raise TypeError('data must be a flat iterable.')
        if not isinstance(data, numpy.ndarray):
            data = numpy.array(data) 
       
        params = Fid._parameters_to_list(p)
        x = numpy.arange(len(data), dtype='f8')
        res = data-cls._f_pks(params, x)
        return res

    @classmethod
    def _f_fitp(cls, data, peaks, frac_gauss=None, method='leastsq'):
        """Fit a section of spectral data with a combination of Gaussian/Lorentzian peaks for deconvolution.
        
        Keyword arguments:
        peaks -- selected peak positions (see peakpicker())
        frac_gauss -- fraction of fitted function to be Gaussian (1 - Guassian, 0 - Lorentzian)
   
        returns:
            fits -- list of fitted peak parameter sets
            
        Note: peaks are fitted by default using the Levenberg-Marquardt algorithm[1]. Other fitting algorithms are available (http://cars9.uchicago.edu/software/python/lmfit/fitting.html#choosing-different-fitting-methods).
        
        [1] Marquardt, Donald W. 'An algorithm for least-squares estimation of nonlinear parameters.' Journal of the Society for Industrial & Applied Mathematics 11.2 (1963): 431-441.
        """
        data = numpy.real(data)
        if not cls._is_flat_iter(data):
            raise TypeError('data must be a flat iterable') 
        if not cls._is_flat_iter(peaks):
            raise TypeError('peaks must be a flat iterable') 
        if any(peak > (len(data)-1)  for peak in peaks):
            raise ValueError('peaks must be within the length of data.')
        if not isinstance(data, numpy.ndarray):
            data = numpy.array(data) 
        p = cls._f_makep(data, peaks, frac_gauss=0.5)
        init_ref = cls._f_conv(p, data)
        if any(peaks+init_ref < 0) or any(peaks+init_ref > len(data)-1):
            init_ref = 0 
        if frac_gauss==None:
            p = cls._f_makep(data, peaks+init_ref, frac_gauss=0.5)
        else:
            p = cls._f_makep(data, peaks+init_ref, frac_gauss=frac_gauss)
        
        params = lmfit.Parameters()
        for parset in range(len(p)):
            current_parset = dict(zip(['offset', 'sigma', 'hwhm', 'amplitude', 'frac_gauss'], p[parset]))
            for k,v in current_parset.items():
                par_name = '%s_%i'%(k, parset)
                params.add(name=par_name, 
                        value=v, 
                        vary=True, 
                        min=0.0)
                if 'offset' in par_name:
                    params[par_name].max = len(data)-1
                if 'frac_gauss' in par_name:
                    params[par_name].max = 1.0
                    if frac_gauss is not None:
                        params[par_name].vary = False
                #if 'sigma' in par_name or 'hwhm' in par_name:
                #    params[par_name].max = 0.01*current_parset['amplitude'] 
                if 'amplitude' in par_name:
                    params[par_name].max = 2.0*data.max()
                    
        try:
            mz = lmfit.minimize(cls._f_res, params, args=([data]), method=method)
            fits = Fid._parameters_to_list(mz.params)
        except:
            fits = None
        return fits

    @classmethod
    def _parameters_to_list(cls, p):
        n_pks = int(len(p)/5)
        params = []
        for i in range(n_pks):
            current_params = [p['%s_%s'%(par, i)].value for par in ['offset', 'sigma', 'hwhm', 'amplitude', 'frac_gauss']]
            params.append(current_params)
        return params


    @classmethod
    def _deconv_datum(cls, list_parameters):
        if len(list_parameters) != 5:
            raise ValueError('list_parameters must consist of five objects.')
        if list_parameters[1] == [] or list_parameters[2] == []:
            return []

        datum, peaks, ranges, frac_gauss, method = list_parameters

        if not cls._is_iter_of_iters(ranges):
            raise TypeError('ranges must be an iterable of iterables') 
        if not all(len(rng) == 2 for rng in ranges):
            raise ValueError('ranges must contain two values.')
        if not all(rng[0] != rng[1] for rng in ranges):
            raise ValueError('data_index must contain different values.')
        if not isinstance(datum, numpy.ndarray):
            datum = numpy.array(datum) 
        if datum.dtype in cls._complex_dtypes:
            raise TypeError('data must be not be complex.')

        fit = []
        for j in zip(peaks, ranges):
            d_slice = datum[j[1][0]:j[1][1]]
            p_slice = j[0]-j[1][0]
            f = cls._f_fitp(d_slice, p_slice, frac_gauss=frac_gauss, method=method)
            f = numpy.array(f).transpose()
            f[0] += j[1][0]
            f = f.transpose()
            fit.append(f)
        return fit

    def deconv(self, method='leastsq', frac_gauss=0.0):
        """

        Deconvolute :attr:`~nmrpy.data_obects.Fid.data` object by fitting a
        series of peaks to the spectrum. These peaks are generated using the parameters
        in :attr:`~nmrpy.data_objects.Fid.peaks`. :attr:`~nmrpy.data_objects.Fid.ranges`
        splits :attr:`~nmrpy.data_objects.Fid.data` up into smaller portions. This
        significantly speeds up deconvolution time.

        :keyword frac_gauss: (0-1) determines the Gaussian fraction of the peaks. Setting this argument to None will fit this parameter as well.

        :keyword method: The fitting method to use. Default is 'leastsq', the Levenberg-Marquardt algorithm, which is usually sufficient. Additional options include:
            
            Nelder-Mead (nelder)
        
            L-BFGS-B (l-bfgs-b)
        
            Conjugate Gradient (cg)
        
            Powell (powell)
        
            Newton-CG  (newton)
        
        """

        if not len(self.data):
            raise AttributeError('data does not exist.')
        if self.data.dtype in self._complex_dtypes:
            raise TypeError('data must be not be complex.')
        if self.peaks is None:
            raise AttributeError('peaks must be picked.')
        if self.ranges is None:
            raise AttributeError('ranges must be specified.')
        print('deconvoluting {}'.format(self.id))
        list_parameters = [self.data, self._grouped_index_peaklist, self._index_ranges, frac_gauss, method]
        self._deconvoluted_peaks = numpy.array([j for i in Fid._deconv_datum(list_parameters) for j in i])
        print('deconvolution completed')


    def plot_ppm(self, **kwargs):
        """
        Plot :attr:`~nmrpy.data_objects.Fid.data`.

        :keyword upper_ppm: upper spectral bound in ppm

        :keyword lower_ppm: lower spectral bound in ppm

        :keyword lw: linewidth of plot 

        :keyword colour: colour of the plot
        """
        plt = Plot()
        plt._plot_ppm(self.data, self._params, **kwargs)
        setattr(self, plt.id, plt)
        plt.fig.show()

    def plot_deconv(self, **kwargs):
        """
        Plot :attr:`~nmrpy.data_objects.Fid.data` with deconvoluted peaks overlaid.

        :keyword upper_ppm: upper spectral bound in ppm

        :keyword lower_ppm: lower spectral bound in ppm

        :keyword lw: linewidth of plot 

        :keyword colour: colour of the plot

        :keyword peak_colour: colour of the deconvoluted peaks

        :keyword residual_colour: colour of the residual signal after subtracting deconvoluted peaks
        """
        plt = Plot()
        plt._plot_deconv(self, **kwargs)
        setattr(self, plt.id, plt)
        plt.fig.show()
 
class FidArray(Base):
    '''

    This object collects several :class:`~nmrpy.data_objects.Fid` objects into
    an array, and it contains all the processing methods necessary for bulk
    processing of these FIDs. It should be considered the parent object for any
    project. The class methods :meth:`~nmrpy.data_objects.FidArray.from_path` and
    :meth:`~nmrpy.data_objects.FidArray.from_data` will instantiate a new
    :class:`~nmrpy.data_objects.FidArray` object from a Varian/Bruker .fid path or
    an iterable of data respectively. Each :class:`~nmrpy.data_objects.Fid` object
    in the array will appear as an attribute of
    :class:`~nmrpy.data_objects.FidArray` with a unique ID of the form 'fidXX',
    where 'XX' is an increasing integer .

    '''
    def __str__(self):
        return 'FidArray of {} FID(s)'.format(len(self.data))

    def get_fid(self, id):
        """
        Return an :class:`~nmrpy.data_objects.Fid` object owned by this object, identified by unique ID. Eg.::

            fid12 = fid_array.get_fid('fid12')

        :arg id: a string id for an :class:`~nmrpy.data_objects.Fid`
        """
        try:
            return getattr(self, id)
        except AttributeError:
            print('{} does not exist.'.format(id))

    def get_fids(self):
        """
        Return a list of all :class:`~nmrpy.data_objects.Fid` objects owned by this :class:`~nmrpy.data_objects.FidArray`.
        """
        fids = [self.__dict__[id] for id in sorted(self.__dict__) if isinstance(self.__dict__[id], Fid)]
        return fids

    def _get_plots(self):
        """
        Return a list of all :class:`~nmrpy.plotting.Plot` objects owned by this :class:`~nmrpy.data_objects.FidArray`.
        """
        plots = [self.__dict__[id] for id in sorted(self.__dict__) if isinstance(self.__dict__[id], Plot)]
        return plots

    def _del_plots(self):
        """
        Deletes all :class:`~nmrpy.plotting.Plot` objects owned by this :class:`~nmrpy.data_objects.FidArray`.
        """
        plots = self._get_plots()
        for plot in plots:
            delattr(self, plot.id)

    @property
    def data(self):
        """
        An array of all :attr:`~nmrpy.data_objects.Fid.data` objects belonging to the :class:`~nmrpy.data_objects.Fid` objects owned by this :class:`~nmrpy.data_objects.FidArray`.
        """
        data = numpy.array([fid.data for fid in self.get_fids()])
        return data

    @property
    def t(self):
        """
        An array of the acquisition time for each FID.
        """
        nfids = len(self.get_fids())
        t = None
        if nfids > 0:
            try:
                acqtime = self._params['acqtime'][0]
                t = (1+numpy.arange(len(self.get_fids())))*acqtime
            except:
                t = numpy.arange(len(self.get_fids()))
        return t

    @property
    def deconvoluted_integrals(self):
        """
        Collected :class:`~nmrpy.data_objects.Fid.deconvoluted_integrals`
        """
        deconvoluted_integrals = []
        for fid in self.get_fids():
            deconvoluted_integrals.append(fid.deconvoluted_integrals)
        return numpy.array(deconvoluted_integrals)

    @property
    def _deconvoluted_peaks(self):
        """
        Collected :class:`~nmrpy.data_objects.Fid._deconvoluted_peaks`
        """
        deconvoluted_peaks = []
        for fid in self.get_fids():
            try:
                deconvoluted_peaks.append(fid._deconvoluted_peaks)
            except:
                deconvoluted_peaks.append([])
        return numpy.array(deconvoluted_peaks)

    def add_fid(self, fid):
        """
        Add an :class:`~nmrpy.data_objects.Fid` object to this :class:`~nmrpy.data_objects.FidArray`, using a unique id.

        :arg fid: an :class:`~nmrpy.data_objects.Fid` instance
        """
        if isinstance(fid, Fid):
            setattr(self, fid.id, fid)
        else:
            raise AttributeError('FidArray requires Fid object.')

    def del_fid(self, fid_id):
        """
        Delete an :class:`~nmrpy.data_objects.Fid` object belonging to this :class:`~nmrpy.data_objects.FidArray`, using a unique id.

        :arg fid_id: a string id for an :class:`~nmrpy.data_objects.Fid`
        """
        if hasattr(self, fid_id):
            if isinstance(getattr(self, fid_id), Fid):
                delattr(self, fid_id)
            else:
                raise AttributeError('{} is not an FID object.'.format(fid_id))
        else:
            raise AttributeError('FID {} does not exist.'.format(fid_id))

    def add_fids(self, fids):
        """
        Add a list of :class:`~nmrpy.data_objects.Fid` objects to this :class:`~nmrpy.data_objects.FidArray`.
        
        :arg fid: a list of :class:`~nmrpy.data_objects.Fid` instances
        """
        if FidArray._is_iter(fids):
            num_fids = len(fids)
            zero_fill = str(len(str(num_fids)))
            for fid_index in range(num_fids):
                try:
                    fid = fids[fid_index]
                    id_str = 'fid{0:0'+zero_fill+'d}'
                    fid.id = id_str.format(fid_index)
                    self.add_fid(fid)
                except AttributeError as e:
                    print(e)

    @classmethod
    def from_data(cls, data):
        """
        Instantiate a new :class:`~nmrpy.data_objects.FidArray` object from a 2D data set of spectral arrays.
        
        :arg data: a 2D data array 
        """
        if not cls._is_iter_of_iters(data):
            raise TypeError('data must be an iterable of iterables.')
        fid_array = cls()
        fids = []
        for fid_index, datum in zip(range(len(data)), data):
            fid_id = 'fid%i'%fid_index
            fid = Fid(id=fid_id, data=datum)
            fids.append(fid)
        fid_array.add_fids(fids)
        return fid_array

    @classmethod
    def from_path(cls, fid_path='.', file_format=None):
        """
        Instantiate a new :class:`~nmrpy.data_objects.FidArray` object from a .fid directory.

        :keyword fidpath: filepath to .fid directory

        :keyword file_path: 'varian' or 'bruker', usually unnecessary

        """
        if not file_format:
            try:
                with open(fid_path, 'rb') as f:
                    return pickle.load(f)
            except:
                print('Not NMRPy data file.')
                importer = Importer(fid_path=fid_path)
                importer.import_fid()
        elif file_format == 'varian':
            importer = VarianImporter(fid_path=fid_path)
            importer.import_fid()
        elif file_format == 'bruker':
            importer = BrukerImporter(fid_path=fid_path)
            importer.import_fid()
        elif file_format == 'nmrpy':
            with open(fid_path, 'rb') as f:
                return pickle.load(f)
       
        if cls._is_iter(importer.data):
            fid_array = cls.from_data(importer.data)
            fid_array._file_format = importer._file_format
            fid_array.fid_path = fid_path
            fid_array._procpar = importer._procpar
            for fid in fid_array.get_fids():
                fid._file_format = fid_array._file_format
                fid.fid_path = fid_array.fid_path
                fid._procpar = fid_array._procpar
            return fid_array 
        else:
            raise IOError('Data could not be imported.')

    def zf_fids(self):
        """ 
        Zero-fill all :class:`~nmrpy.data_objects.Fid` objects owned by this :class:`~nmrpy.data_objects.FidArray`
        """
        for fid in self.get_fids():
            fid.zf()

    def emhz_fids(self, lb=5.0):
        """ 
        Apply line-broadening (apodisation) to all :class:`nmrpy.~data_objects.Fid` objects owned by this :class:`~nmrpy.data_objects.FidArray`

        :keyword lb: degree of line-broadening in Hz.
        """
        for fid in self.get_fids():
            fid.emhz(lb=lb)

    def ft_fids(self, mp=True, cpus=None):
        """ 
        Fourier-transform all FIDs.

        :keyword mp: parallelise over multiple processors, significantly reducing computation time

        :keyword cpus: defines number of CPUs to utilise if 'mp' is set to True
        """
        if mp:
            fids = self.get_fids()
            list_params = [[fid.data, fid._file_format] for fid in fids]
            ft_data = self._generic_mp(Fid._ft, list_params, cpus)
            for fid, datum in zip(fids, ft_data):
                fid.data = datum
                fid._flags['ft'] = True
        else: 
            for fid in self.get_fids():
                fid.ft()
        print('Fourier-transformation completed')

    def real_fids(self):
        """ 
        Discard imaginary component of FID data sets.

        """
        for fid in self.get_fids():
            fid.real()

    def norm_fids(self):
        """ 
        Normalise FIDs by maximum data value in :attr:`~nmrpy.data_objects.FidArray.data`.

        """
        dmax = self.data.max()
        for fid in self.get_fids():
            fid.data = fid.data/dmax

    def phase_correct_fids(self, method='leastsq', mp=True, cpus=None):
        """ 
        Apply automatic phase-correction to all :class:`~nmrpy.data_objects.Fid` objects owned by this :class:`~nmrpy.data_objects.FidArray`

        :keyword method: see :meth:`~nmrpy.data_objects.Fid.phase_correct`

        :keyword mp: parallelise the phasing process over multiple processors, significantly reducing computation time

        :keyword cores: defines number of CPUs to utilise if 'mp' is set to True
        """
        if mp: 
            fids = self.get_fids()
            if not all(fid.data.dtype in self._complex_dtypes for fid in fids):
                raise TypeError('Only complex data can be phase-corrected.')
            if not all(fid._flags['ft'] for fid in fids):
                raise ValueError('Only Fourier-transformed data can be phase-corrected.')
            list_params = [[fid.data, method] for fid in fids]
            phased_data = self._generic_mp(Fid._phase_correct, list_params, cpus)
            for fid, datum in zip(fids, phased_data):
                fid.data = datum
        else:
            for fid in self.get_fids():
                fid.phase_correct(method=method)
        print('phase-correction completed')

    def baseline_correct_fids(self, deg=2):
        """ 
        Apply baseline-correction to all :class:`~nmrpy.data_objects.Fid` objects owned by this :class:`~nmrpy.data_objects.FidArray`

        :keyword deg: degree of the baseline polynomial (see :meth:`~nmrpy.data_objects.Fid.baseline_correct`)
        """
        for fid in self.get_fids():
            fid.baseline_correct(deg=deg)
        print('baseline-correction completed')

    @property
    def _data_traces(self):
        return self.__data_traces

    @_data_traces.setter
    def _data_traces(self, data_traces):
        self.__data_traces = data_traces 

    @property
    def _index_traces(self):
        return self.__index_traces

    @_index_traces.setter
    def _index_traces(self, index_traces):
        self.__index_traces = index_traces 

    @property
    def integral_traces(self):
        return self._integral_traces

    @integral_traces.setter
    def integral_traces(self, integral_traces):
        self._integral_traces = integral_traces 

    def deconv_fids(self, mp=True, cpus=None, method='leastsq', frac_gauss=0.0):
        """ 
        Apply deconvolution to all :class:`~nmrpy.data_objects.Fid` objects owned by this :class:`~nmrpy.data_objects.FidArray`, using the :attr:`~nmrpy.data_objects.Fid.peaks` and  :attr:`~nmrpy.data_objects.Fid.ranges` attribute of each respective :class:`~nmrpy.data_objects.Fid`.

        :keyword method: see :meth:`~nmrpy.data_objects.Fid.phase_correct`

        :keyword mp: parallelise the phasing process over multiple processors, significantly reduces computation time

        :keyword cores: defines number of CPUs to utilise if 'mp' is set to True, default is n-1 cores
        """
        if mp: 
            fids = self.get_fids()
            if not all(fid._flags['ft'] for fid in fids):
                raise ValueError('Only Fourier-transformed data can be deconvoluted.')
            list_params = [[fid.data, fid._grouped_index_peaklist, fid._index_ranges, frac_gauss, method] for fid in fids]
            deconv_datum = self._generic_mp(Fid._deconv_datum, list_params, cpus)
            for fid, datum in zip(fids, deconv_datum):
                fid._deconvoluted_peaks = numpy.array([j for i in datum for j in i])
        else:
            for fid in self.get_fids():
                fid.deconv(frac_gauss=frac_gauss)
        print('deconvolution completed')

    def ps_fids(self, p0=0.0, p1=0.0):
        """
        Apply manual phase-correction to all :class:`~nmrpy.data_objects.Fid` objects owned by this :class:`~nmrpy.data_objects.FidArray`

        :keyword p0: Zero order phase in degrees

        :keyword p1: First order phase in degrees
        """
        for fid in self.get_fids():
            fid.ps(p0=p0, p1=p1)  

    @staticmethod
    def _generic_mp(fcn, iterable, cpus):
        if cpus is None:
            cpus = cpu_count()-1
        proc_pool = Pool(cpus)
        result = proc_pool.map(fcn, iterable)
        proc_pool.close()
        proc_pool.join()
        return result


    def plot_array(self, **kwargs):
        """
        Plot :attr:`~nmrpy.data_objects.FidArray.data`.

        :keyword upper_index: upper index of array (None)

        :keyword lower_index: lower index of array (None)

        :keyword upper_ppm: upper spectral bound in ppm (None)

        :keyword lower_ppm: lower spectral bound in ppm (None)

        :keyword lw: linewidth of plot (0.5)

        :keyword azim: starting azimuth of plot (-90)

        :keyword elev: starting elevation of plot (40)

        :keyword filled: True=filled vertices, False=lines (False)

        :keyword show_zticks: show labels on z axis (False)

        :keyword labels: under development (None)

        :keyword colour: plot spectra with colour spectrum, False=black (True)

        :keyword filename: save plot to .pdf file (None)
        """
        plt = Plot()
        plt._plot_array(self.data, self._params, **kwargs)
        setattr(self, plt.id, plt)
        plt.fig.show()

    def plot_deconv_array(self, **kwargs):
        """
        Plot all :attr:`~nmrpy.data_objects.Fid.data` with deconvoluted peaks overlaid.

        :keyword upper_index: upper index of Fids to plot

        :keyword lower_index: lower index of Fids to plot

        :keyword upper_ppm: upper spectral bound in ppm

        :keyword lower_ppm: lower spectral bound in ppm

        :keyword data_colour: colour of the plotted data ('k')

        :keyword summed_peak_colour: colour of the plotted summed peaks ('r')

        :keyword residual_colour: colour of the residual signal after subtracting deconvoluted peaks ('g')

        :keyword data_filled: fill state of the plotted data (False)

        :keyword summed_peak_filled: fill state of the plotted summed peaks (False)

        :keyword residual_filled: fill state of the plotted residuals (False)

        :keyword figsize: [x, y] size of plot ([15, 7.5])

        :keyword lw: linewidth of plot (0.3)

        :keyword azim: azimuth of 3D axes (-90)

        :keyword elev: elevation of 3D axes (20)


        """
        plt = Plot()
        plt._plot_deconv_array(self.get_fids(), 
            summed_peak_filled=True,
            **kwargs)
        setattr(self, plt.id, plt)
        plt.fig.show()

    def peakpicker(self, fid_number=None, assign_only_to_index=True, voff=0.3):
        """

        Instantiate peak-picker GUI widget for 
        :attr:`~nmrpy.data_objects.Fid.data`, and apply selected
        :attr:`~nmrpy.data_objects.Fid.peaks` and
        :attr:`~nmrpy.data_objects.Fid.ranges` to all :class:`~nmrpy.data_objects.Fid`
        objects owned by this :class:`~nmrpy.data_objects.FidArray`. See
        :meth:`~nmrpy.data_objects.Fid.peakpicker`.

        :keyword fid_number: list or number, index of :class:`~nmrpy.data_objects.Fid` to use
        for peak-picking. If None, data array is plotted.

        :keyword assign_only_to_index: if True, assigns selections only to
        :class:`~nmrpy.data_objects.Fid` objects indexed by fid_number, if False,
        assigns to all

        :keyword voff: vertical offset for spectra
        """
        global _peakpicker_widget
        fids = self.get_fids()
        if fid_number is not None:
            if not Fid._is_iter(fid_number):
                fid_number = [fid_number]
        else:
            fid_number = range(len(fids))

        plot_label = 'Left - select peak\nMiddle - delete nearest peak\nCtrl/Middle - delete range\nDrag Right - select range'
        _peakpicker_widget = DataPeakRangeSelector(self, 
                y_indices=fid_number,
                voff=voff, 
                label=plot_label)

        if len(_peakpicker_widget.ranges) > 0 and len(_peakpicker_widget.peaks) > 0:
            ranges = _peakpicker_widget.ranges
            peaks = []
            for peak in _peakpicker_widget.peaks:
                for rng in ranges:
                    if peak >= rng[1] and peak <= rng[0]:
                        peaks.append(peak)
        else:
            peaks = None
            ranges = None

        if assign_only_to_index:
            for fid in [fids[i] for i in fid_number]:
                fid.peaks = peaks
                fid.ranges = ranges
        else:       
            for fid in fids:
                fid.peaks = peaks
                fid.ranges = ranges

    def peakpicker_traces(self, 
            voff=0.01, 
            lw=1):
        """
        Instantiates a widget to pick peaks and ranges employing a polygon
        shape. This is useful for picking peaks that are subject to drift and peaks
        that appear (or disappear) during the course of an experiment.
        """
        if self.data is None:
            raise AttributeError('No FIDs.')
        global _peakpicker_widget 
        _peakpicker_widget = DataTraceRangeSelector(
            self,
            peaks=None,
            ranges=None,
            voff=voff,
            lw=lw,
            )

        traces = [[i[0], j[1]] for i, j in zip(_peakpicker_widget.data_traces, _peakpicker_widget.index_traces)]

        self._set_all_peaks_ranges_from_traces_and_spans(
            traces, 
            _peakpicker_widget.spans)

    def _set_all_peaks_ranges_from_traces_and_spans(self, traces, spans): 
        traces = [dict(zip(i[1], i[0])) for i in traces]
        fids = self.get_fids()
        fids_i = range(len(self.data))
        for i in fids_i:
            peaks = []
            for j in traces:
                if i in j:
                    peak = j[i]
                    for rng in spans:
                        if peak >= min(rng) and peak <= max(rng):
                            peaks.append(peak)
            fids[i].peaks = peaks 
            ranges = []
            for rng in spans: 
                if any((peaks>min(rng))*(peaks<max(rng))):
                    ranges.append(rng)
            if ranges == []:
                ranges = None
            fids[i].ranges = ranges 
          

    def _get_all_summed_peakshapes(self):
        """
        Returns peakshapes for all FIDs
        """
        peaks = []
        for fid in self.get_fids():
            #x = numpy.arange(len(self.get_fids()[0].data))
            x = numpy.arange(len(self.get_fids()[0].data))
            peaks.append(Fid._f_pks(fid._deconvoluted_peaks, x))
        return peaks

    def _get_all_list_peakshapes(self):
        """
        Returns peakshapes for all FIDs
        """
        peaks = []
        for fid in self.get_fids():
            #x = numpy.arange(len(self.get_fids()[0].data))
            x = numpy.arange(len(self.get_fids()[0].data))
            peaks.append(Fid._f_pks_list(fid._deconvoluted_peaks, x))
        return peaks

    def _get_truncated_peak_shapes_for_plotting(self):
        """
        Produces a set of truncated deconvoluted peaks for plotting.
        """
        peakshapes = self._get_all_list_peakshapes()
        ppms = [fid._ppm for fid in self.get_fids()]
        peakshapes_short_x = []
        peakshapes_short_y = []
        for ps, ppm in zip(peakshapes, ppms):
            pk_y = []
            pk_x = []
            for pk in ps:
                pk_ind = pk > 0.1*pk.max()
                pk_x.append(ppm[pk_ind])
                pk_y.append(pk[pk_ind])
            peakshapes_short_x.append(pk_x)
            peakshapes_short_y.append(pk_y)
        return peakshapes_short_x, peakshapes_short_y

    def select_integral_traces(self, voff=0.01, lw=1):
        """

        Instantiate a trace-selection widget to identify deconvoluted peaks.
        This can be useful when data are subject to drift. Selected traces on the data
        array are translated into a set of nearest deconvoluted peaks, and saved in a
        dictionary: :attr:`~nmrpy.data_objects.FidArray.integral_traces`.

        :keyword voff: vertical offset fraction (0.01)

        :keyword lw: linewidth of plot
        """
        if self.data is None:
            raise AttributeError('No FIDs.')
        if self.deconvoluted_integrals is None:
            raise AttributeError('No integrals.')
        peakshapes = self._get_all_summed_peakshapes()
        #pk_x, pk_y = self._get_truncated_peak_shapes_for_plotting()
        global _select_trace_widget
        _select_trace_widget = DataTraceSelector(self, 
            extra_data=peakshapes, 
            extra_data_colour='b', 
            voff=voff, 
            lw=lw)

        self._data_traces = [dict(zip(i[1], i[0])) for i in _select_trace_widget.data_traces]
        self._index_traces = [dict(zip(i[1], i[0])) for i in _select_trace_widget.index_traces]

        decon_peaks = []
        for i in self._deconvoluted_peaks:
            if len(i):
                decon_peaks.append(i.transpose()[0])
            else:
                decon_peaks.append(None)

        #traces = [[i[0], j[1]] for i, j in zip(_select_trace_widget.data_traces, _select_trace_widget.index_traces)]
        trace_dict = {}
        for t in range(len(self._index_traces)):
            trace = self._index_traces[t]
            integrals = {}
            for fid, indx in trace.items():
                try:
                    integrals[fid] = numpy.argmin(abs(decon_peaks[fid]-indx))
                except:
                    integrals[fid] = None
            trace_dict[t] = integrals
        last_fid = (len(self.get_fids())-1)
        for i in trace_dict:
            tmin = min(trace_dict[i])
            tminval = trace_dict[i][tmin]
            if tmin > 0:
                for j in range(0, tmin):
                    trace_dict[i][j] = tminval
            tmax = max(trace_dict[i])
            tmaxval = trace_dict[i][tmax]
            if tmax < last_fid:
                for j in range(tmax, last_fid+1):
                    trace_dict[i][j] = tmaxval
        self.integral_traces = trace_dict
                
    def get_integrals_from_traces(self):
        """
        Returns a dictionary of integral values for all
        :class:`~nmrpy.data_objects.Fid` objects calculated from trace dictionary
        :attr:`~nmrpy.data_objects.FidArray.integral_traces`.
        """
        if self.deconvoluted_integrals is None:
            raise AttributeError('No integrals.')
        if self.integral_traces is None:
            raise AttributeError('No integral traces. First run select_integral_traces().')
        integrals_set = {}
        decon_set = self.deconvoluted_integrals 
        for i, tr in self.integral_traces.items():
            tr_keys = numpy.array([fid for fid in tr.keys()])
            tr_vals = numpy.array([val for val in tr.values()])
            tr_sort = numpy.argsort(tr_keys)
            tr_keys = tr_keys[tr_sort]
            tr_vals = tr_vals[tr_sort]
            integrals = decon_set[tr_keys, tr_vals]
            integrals_set[i] = integrals    
        return integrals_set

    def save_to_file(self, filename=None):
        """
        Save :class:`~nmrpy.data_objects.FidArray` object to file, including all objects owned.

        :keyword filename: filename to save :class:`~nmrpy.data_objects.FidArray` to

        """
        if filename is None:
            filename = 'data.nmrpy'
        if not isinstance(filename, str):
            raise TypeError('filename must be a string.')
        #delete all matplotlib plots to reduce file size
        self._del_plots()
        for fid in self.get_fids():
            fid._del_plots()
        with open(filename, 'wb') as f:
            pickle.dump(self, f)
  
class Importer(Base):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.data = None

    @property
    def data(self):
        return self.__data

    @data.setter
    def data(self, data):
        if data is None:
            self.__data = data
        elif data.dtype in self._complex_dtypes:
            if Importer._is_iter_of_iters(data):
                self.__data = data
            elif Importer._is_iter(data):
                self.__data = numpy.array([data])
            else:
                raise TypeError('data must be iterable.')
        else:
            raise TypeError('data must be complex.')


    def import_fid(self):
        """
        This will first attempt to import Bruker data. Failing that, Varian.
        """
        try:
            print('Attempting Bruker')
            procpar, data = nmrglue.bruker.read(self.fid_path)
            self.data = data
            self._procpar = procpar['acqus']
            self._file_format = 'bruker'
            self.data = nmrglue.bruker.remove_digital_filter(procpar, self.data)
            return
        except (FileNotFoundError, OSError):
            print('fid_path does not specify a valid .fid directory.')
            return 
        except TypeError:
            print('probably not Bruker data')
        try: 
            print('Attempting Varian')
            procpar, data = nmrglue.varian.read(self.fid_path)
            self._procpar = procpar
            self.data = data 
            self._file_format = 'varian'
            return
        except TypeError:
            print('probably not Varian data')

class VarianImporter(Importer):

    def import_fid(self):
        try:
            procpar, data = nmrglue.varian.read(self.fid_path)
            self.data = data 
            self._procpar = procpar
            self._file_format = 'varian'
        except FileNotFoundError:
            print('fid_path does not specify a valid .fid directory.')
        except OSError:
            print('fid_path does not specify a valid .fid directory.')
        
class BrukerImporter(Importer):

    def import_fid(self):
        try:
            procpar, data = nmrglue.bruker.read(self.fid_path)
            self.data = data
            self._procpar = procpar['acqus']
            self._file_format = 'bruker'
            self.data = nmrglue.bruker.remove_digital_filter(procpar, self.data)
        except FileNotFoundError:
            print('fid_path does not specify a valid .fid directory.')
        except OSError:
            print('fid_path does not specify a valid .fid directory.')

if __name__ == '__main__':
    pass
