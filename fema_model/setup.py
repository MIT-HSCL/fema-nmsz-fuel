from distutils.core import setup
setup(
  name = 'fema_model',
  packages = ['fema_model'],
  version = '0.1',
  license='NONE',
  description = 'An app to run FEMA models',
  author = 'Connor Makowski',
  author_email = 'conmak@mit.edu',
  url = 'https://github.com/MIT-CAVE/fema',
  download_url = 'https://github.com/MIT-CAVE/fema',
  keywords = ['FEMA','MODEL'],
  install_requires=[
    'pamda==0.0.10'
  ],
  classifiers=[
    'Development Status :: 3 - Alpha',
    'Intended Audience :: Developers',
    'Topic :: Software Development :: Build Tools',
    'License :: OSI Approved :: NONE',
    'Programming Language :: Python :: 3',
    'Programming Language :: Python :: 3.4',
    'Programming Language :: Python :: 3.5',
    'Programming Language :: Python :: 3.6',
    'Programming Language :: Python :: 3.7',
  ],
)
