# nDisplay Merger

When rendering nDisplay with the Movie Render Queue in Unreal 5.1 it exports one image per viewport per frame. It doesn't merge the viewports the way it is specified in the Output Mapping window. This program takes a nDisplay configuration file and the folder with the renders and merges the images of each viewport following the instructions in the nDisplay config.

![image-20230414225309635](C:\Users\benja\Documents\Cosas Benja\Proyectos\Pi\Cinta_Bolivia\nDisplayRenderMerger\assets\image-20230414225309635.png)

## Setup

```
pip install -r requirements.txt
```



# Compile to Executable

```
python -m PyInstaller --onefile --windowed ui.py --additional-hooks-dir=.
```



## Usage

```
python .\nDisplayMerger.py .\MovieRenders nDisplayConfig.ndisplay
```



This program was made together with ChatGPT by OpenAI. The initial prompt was:

```
I'm using Unreal Engine 5.1.1 to render a scene using the Movie Render Queue with nDisplay. It generates for each time-frame a separate image for each nDisplay Viewport. I would like for each time-frame to create a single composed image that has all the viewports next to each other. I have an nDisplay config file which has the information on how each viewport should be placed in the composed image. This nDisplay file has the information in json format.
I want you to create a python program that takes two parameters: path to a directory where the images are located and an nDisplay config file. And it should output one composited image for each time-frame.
The naming scheme for the image files in the directory is as follows: {LevelSequenceName}.nDisplayLit.{ViewportName}.{TimeFrameNumber}.jpeg
The output images should follow this naming scheme: {LevelSequenceName}.nDisplayLit.{TimeFrameNumber}.jpeg

I'm going to give you an example of the relevant part of an ndisplay config file (nDisplayConfig.ndisplay). You only need the information in the "region" section of each viewport and the "window" section of the first node. In this example the viewports are "Piso", "Segmento0" and "Segmento1". Here is the example:

{
	"nDisplay":
	{
		"description": "",
		"version": "5.00",
		"assetPath": "/Game/Migrated/nDisplay_Cinta_Unreal_2.nDisplay_Cinta_Unreal_2",
		"misc":
		{
			"bFollowLocalPlayerCamera": false,
			"bExitOnEsc": true,
			"bOverrideViewportsFromExternalConfig": false
		},
		"cluster":
		{
			"primaryNode":
			{
				"id": "Node_0",
				"ports":
				{
					"ClusterSync": 41001,
					"ClusterEventsJson": 41003,
					"ClusterEventsBinary": 41004
				}
			},
			"sync":
			{
				"renderSyncPolicy":
				{
					"type": "ethernet",
					"parameters":
					{
					}
				},
				"inputSyncPolicy":
				{
					"type": "ReplicatePrimary",
					"parameters":
					{
					}
				}
			},
			"failover":
			{
				"failoverPolicy": "Disabled"
			},
			"nodes":
			{
				"Node_0":
				{
					"host": "127.0.0.1",
					"sound": false,
					"fullScreen": false,
					"window":
					{
						"x": 0,
						"y": 0,
						"w": 2400,
						"h": 3840
					},
					"postprocess":
					{
					},
					"viewports":
					{
						"Piso":
						{
							"camera": "",
							"bufferRatio": 1,
							"gPUIndex": -1,
							"allowCrossGPUTransfer": false,
							"isShared": false,
							"overscan":
							{
								"bEnabled": false,
								"mode": "percent",
								"left": 0,
								"right": 0,
								"top": 0,
								"bottom": 0,
								"oversize": true
							},
							"region":
							{
								"x": 0,
								"y": 0,
								"w": 1200,
								"h": 1306
							},
							"projectionPolicy":
							{
								"type": "simple",
								"parameters":
								{
									"mesh_component": "",
									"screen": "Piso1"
								}
							}
						},
						"Segmento1":
						{
							"camera": "",
							"bufferRatio": 1,
							"gPUIndex": -1,
							"allowCrossGPUTransfer": false,
							"isShared": false,
							"overscan":
							{
								"bEnabled": false,
								"mode": "percent",
								"left": 0,
								"right": 0,
								"top": 0,
								"bottom": 0,
								"oversize": true
							},
							"region":
							{
								"x": 1200,
								"y": 2637,
								"w": 1200,
								"h": 293
							},
							"projectionPolicy":
							{
								"type": "simple",
								"parameters":
								{
									"screen": "Segmento1"
								}
							}
						},
						"Segmento0":
						{
							"camera": "",
							"bufferRatio": 1,
							"gPUIndex": -1,
							"allowCrossGPUTransfer": false,
							"isShared": false,
							"overscan":
							{
								"bEnabled": false,
								"mode": "percent",
								"left": 0,
								"right": 0,
								"top": 0,
								"bottom": 0,
								"oversize": true
							},
							"region":
							{
								"x": 1200,
								"y": 2930,
								"w": 1200,
								"h": 178
							},
							"projectionPolicy":
							{
								"type": "simple",
								"parameters":
								{
									"screen": "Segmento0"
								}
							}
						},
					},
					"outputRemap":
					{
						"bEnable": false,
						"dataSource": "mesh",
						"staticMeshAsset": "",
						"externalFile": ""
					}
				}
			}
		},
		"customParameters":
		{
		},
		"diagnostics":
		{
			"simulateLag": false,
			"minLagTime": 0.0099999997764825821,
			"maxLagTime": 0.30000001192092896
		}
	}
}
```

