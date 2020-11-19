# FullMonteWeb
A web-application for runnig and visualizing the results of the FullMonte simulator

Changelist

0.0.1 Added 3 new views: tutorial, simulation and visualization

This branch aims to deploy the website to Amazon Elastic Beanstalk.
Everything works but VTK. Amazon Linux does not work well with VTK packages, particularly OpenGL。
Therefore all VTK-related modules are removed in this version.
