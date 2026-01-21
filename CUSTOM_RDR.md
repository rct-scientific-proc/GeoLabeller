import numpy as np      # Must return a np array for the image data

# Place your imports here.

def reader(filename):
    # Place code here
    # Should return a list containing ground 1x4 control points
    # A ground control point should have the values [x, y, lat, lon]
    # Where x is the x pixel location, y is the y pixel location, lat is the latitude, and lon is the longitude
    # image_data is a numpy array representing a row major image either grayscale or RGB or RGBA
    #
    # gcps = [
    #   [x1, y1, lat1, lon1],
    #   [x2, y2, lat2, lon2],
    #   [x3, y3, lat3, lon3],
    #   [x4, y4, lat4, lon4]
    # ]

    return image_data, gcps





